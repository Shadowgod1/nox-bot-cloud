"""One bot turn: admission -> prompt -> PII mask -> LLM with tools -> restore -> send (spec 5.1 steps 4-8)."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from iler import settings_store, usage
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
HANDOVER_REPLY = "Передаю ваш вопрос администратору, он скоро ответит."
AFTER_HOURS_REPEAT = timedelta(hours=12)  # the after-hours text goes to a conversation at most this often
NO_COINS_REPEAT_MINUTES = 24 * 60  # coin-limit handover: minimum pause and reply repeat interval


def _not_configured(bot) -> bool:
    """The owner has not written the bot prompt yet (`prompt` field may not exist in older settings)."""
    value = getattr(bot, "prompt", None)
    return value is not None and not str(value).strip()


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


async def _handle_llm_failure(conv, error: LlmError, turn_id: str) -> None:
    bot = await settings_store.get("bot", BotSettings)
    await store.set_status(conv["id"], "needs_operator", pause_minutes=bot.handover_pause_minutes)
    who = conv["name"] or "клиент"
    phone = f" +{conv['phone']}" if conv["phone"] else ""
    await sender.notify_admins(
        f"Бот не смог ответить ({who}{phone}): ошибка LLM. Диалог ждёт оператора в админке.",
        key=f"llmfail:{conv['id']}:{turn_id}",  # a retried turn for the same message notifies once
    )
    log.warning("LLM failed for conversation %s (%s): %s", conv["id"], mask_phone_for_log(conv["phone"]), error)


async def _handover_no_coins(conv, turn_id: str) -> None:
    """The monthly coin limit is used up: the client is told an administrator will answer, the
    conversation waits for the operator and the admins are told (once per conversation per day).

    The pause is at least a day and the reply is not repeated if it already went out within a day, so a
    client who keeps writing while the limit is exhausted does not get the same phrase over and over."""
    bot = await settings_store.get("bot", BotSettings)
    pause = max(bot.handover_pause_minutes, NO_COINS_REPEAT_MINUTES)
    await store.set_status(conv["id"], "needs_operator", pause_minutes=pause)
    prev = await store.last_bot_message(conv["id"])
    recent = timedelta(minutes=NO_COINS_REPEAT_MINUTES)
    if prev is not None and prev["text"] == HANDOVER_REPLY and prev["created_at"] > datetime.now(UTC) - recent:
        log.info("conversation %s: coin-limit handover reply already sent recently", conv["id"])
    else:
        await sender.send_reply(conv["id"], sender.split_reply(HANDOVER_REPLY), turn_id=turn_id)
    who = conv["name"] or "клиент"
    phone = f" +{conv['phone']}" if conv["phone"] else ""
    await sender.notify_admins(
        f"Лимит коинов ILER исчерпан: бот не отвечает сам. Сообщение ({who}{phone}) ждёт оператора в админке.",
        key=f"coins-conv:{conv['id']}:{datetime.now(UTC):%Y-%m-%d}",
    )
    log.info("conversation %s handed to the operator: coin limit reached", conv["id"])


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
        if _not_configured(bot):
            log.warning("conversation %s: no reply, the bot prompt is not configured", conversation_id)
            return TurnOutcome()
        rows = await store.recent_messages(conversation_id, bot.memory_messages)
        last = rows[-1] if rows else None
        if last is None or last["author"] != "client":
            return TurnOutcome()  # already answered (duplicate timer, recovery race) or nothing to answer
        turn_id = str(last["id"])  # deterministic: re-running the turn for the same message never re-sends
        if decision.action == "fixed_message":
            parts = sender.split_reply(bot.after_hours_message)
            prev = await store.last_bot_message(conversation_id)
            if parts and prev is not None and prev["text"] == parts[-1] and prev["created_at"] > now - AFTER_HOURS_REPEAT:
                log.info("conversation %s: after-hours message already sent recently", conversation_id)
                return TurnOutcome()
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
        # the router enforces the coin limit and refreshes the coin accounting (AD-3)
        result = await router.complete_with_tools(messages, tools, ctx, all_settings.llm)
    except usage.CoinsExhausted:
        if dry_run:
            raise
        await _handover_no_coins(conv, turn_id)
        return TurnOutcome(handed_over=True)
    except LlmError as e:
        if dry_run:
            raise
        await _handle_llm_failure(conv, e, turn_id)
        return TurnOutcome(handed_over=True)

    tool_calls = [{**c, "args": masker.restore_obj(c["args"])} for c in result.tool_calls]
    handed_over = any(c["name"] == HANDOVER_TOOL and not c["error"] for c in tool_calls)
    text = masker.restore(result.text).strip()
    if handed_over and (not text or text == router.STUB_REPLY):
        text = HANDOVER_REPLY  # the model handed over without a word to the client
    parts = sender.split_reply(text)
    outcome = TurnOutcome(reply_parts=parts, tool_calls=tool_calls, handed_over=handed_over)
    if dry_run or not parts:
        return outcome

    dropped = TurnOutcome(reply_parts=[], tool_calls=tool_calls, handed_over=handed_over)
    # The operator may have taken over while the LLM was thinking: then the bot stays silent.
    fresh = await store.get_conversation(conversation_id)
    if fresh is None or (not handed_over and admission.is_paused(fresh["status"], fresh["paused_until"],
                                                                 datetime.now(UTC))):
        log.info("conversation %s paused during the turn; reply dropped", conversation_id)
        return dropped
    # The client wrote again while the LLM was thinking: the turn queued behind this one answers both
    # (it sees the whole history), so this reply would only be outdated. A handover reply still goes out:
    # the queued turn will be skipped by the pause.
    if not handed_over and await store.has_client_message_after(conversation_id, int(turn_id)):
        log.info("conversation %s: newer client message arrived during the turn; reply dropped", conversation_id)
        return dropped
    await sender.send_reply(conversation_id, parts, turn_id=turn_id)
    return outcome
