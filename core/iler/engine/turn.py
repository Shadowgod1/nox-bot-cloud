"""One bot turn: admission -> prompt -> PII mask -> LLM with tools -> restore -> send (spec 5.1 steps 4-8)."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from iler import settings_store
from iler.config import get_settings
from iler.engine import admission, prompt, sender, store
from iler.engine.pii import PiiMasker, mask_phone_for_log
from iler.llm import router
from iler.llm.base import ChatMessage, LlmError
from iler.settings_models import BotSettings
from iler.tools import registry
from iler.tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger("iler.engine.turn")

HANDOVER_TOOL = "handover_to_operator"


@dataclass
class TurnOutcome:
    reply_parts: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    handed_over: bool = False


class _PiiRestoringTool:
    """Wraps a tool so it receives the real values behind [PHONE_1]-style tokens in its arguments."""

    def __init__(self, tool: Tool, masker: PiiMasker):
        self._tool = tool
        self._masker = masker
        self.name = tool.name
        self.description = tool.description
        self.parameters = tool.parameters

    def enabled(self, settings) -> bool:
        return self._tool.enabled(settings)

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return await self._tool.run(ctx, self._masker.restore_obj(args))


def _tz() -> tuple[ZoneInfo, str]:
    name = get_settings().tz
    try:
        return ZoneInfo(name), name
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC"), "UTC"


async def _handle_llm_failure(conv, error: LlmError) -> None:
    bot = await settings_store.get("bot", BotSettings)
    await store.set_status(conv["id"], "needs_operator", pause_minutes=bot.handover_pause_minutes)
    who = conv["name"] or "клиент"
    phone = f" +{conv['phone']}" if conv["phone"] else ""
    await sender.notify_admins(
        f"Бот не смог ответить ({who}{phone}): ошибка LLM. Диалог ждёт оператора в админке.",
        key=f"llmfail:{conv['id']}:{datetime.now(UTC).timestamp():.0f}",
    )
    log.warning("LLM failed for conversation %s (%s): %s", conv["id"], mask_phone_for_log(conv["phone"]), error)


async def run_turn(conversation_id: int | None, *, dry_run: bool = False,
                   history: list[ChatMessage] | None = None, phone: str = "") -> TurnOutcome:
    """Run one turn.

    Live (`dry_run=False`): history comes from the DB, admission rules apply, the reply is sent to WhatsApp.
    Dry run (test chat): `history` is used instead of the DB, admission is skipped, nothing is sent or
    written (tools get `ToolContext(dry_run=True)`); an `LlmError` propagates to the caller.
    """
    all_settings = await settings_store.load_all()
    bot = all_settings.bot
    tz, tz_name = _tz()
    now = datetime.now(UTC)
    now_local = now.astimezone(tz)
    conv = None
    client_name = ""
    turn_id = "dry"

    if dry_run:
        chat = list(history or [])
    else:
        if conversation_id is None:
            return TurnOutcome()
        conv = await store.get_conversation(conversation_id)
        if conv is None:
            return TurnOutcome()
        decision = admission.decide(conv, bot, now, now_local)
        if decision.action == "skip":
            log.info("conversation %s: no reply (%s)", conversation_id, decision.reason)
            return TurnOutcome()
        rows = await store.recent_messages(conversation_id, bot.memory_messages)
        last = rows[-1] if rows else None
        if last is None or last["author"] != "client":
            return TurnOutcome()  # already answered (duplicate timer, recovery race) or nothing to answer
        turn_id = str(last["id"])  # deterministic: re-running the turn for the same message never re-sends
        if decision.action == "fixed_message":
            parts = sender.split_reply(bot.after_hours_message)
            await sender.send_reply(conversation_id, parts, turn_id=turn_id)
            return TurnOutcome(reply_parts=parts)
        chat = prompt.history_from_rows(rows)
        client_name = conv["name"]
        phone = conv["phone"]

    masker = PiiMasker()
    messages = prompt.build_messages(bot, chat, now_local, tz_name, client_name)
    messages = [messages[0]] + [ChatMessage(role=m.role, content=masker.mask(m.content)) for m in messages[1:]]
    tools = [_PiiRestoringTool(t, masker) for t in registry.enabled_tools(all_settings)]
    ctx = ToolContext(
        conversation_id=conversation_id,
        phone=phone,
        settings=bot,
        now=now,
        dry_run=dry_run,
        extra={"turn_id": turn_id, "client_name": client_name, "jid": conv["chat_jid"] if conv else ""},
    )

    try:
        result = await router.complete_with_tools(messages, tools, ctx, all_settings.llm)
    except LlmError as e:
        if dry_run:
            raise
        await _handle_llm_failure(conv, e)
        return TurnOutcome(handed_over=True)

    tool_calls = [{**c, "args": masker.restore_obj(c["args"])} for c in result.tool_calls]
    handed_over = any(c["name"] == HANDOVER_TOOL and not c["error"] for c in tool_calls)
    parts = sender.split_reply(masker.restore(result.text))
    outcome = TurnOutcome(reply_parts=parts, tool_calls=tool_calls, handed_over=handed_over)
    if dry_run or not parts:
        return outcome

    # The operator may have taken over while the LLM was thinking: then the bot stays silent.
    fresh = await store.get_conversation(conversation_id)
    if fresh is None or (not handed_over and admission.is_paused(fresh["status"], fresh["paused_until"],
                                                                 datetime.now(UTC))):
        log.info("conversation %s paused during the turn; reply dropped", conversation_id)
        return TurnOutcome(reply_parts=[], tool_calls=tool_calls, handed_over=handed_over)
    await sender.send_reply(conversation_id, parts, turn_id=turn_id)
    return outcome
