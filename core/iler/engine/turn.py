"""One bot turn: admission -> prompt -> PII mask -> LLM with tools -> restore -> send (spec 5.1 steps 4-8)."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from iler import reply_rules, settings_store, usage
from iler.config import brand_company, brand_name, gateway_mode, get_settings
from iler.engine import admission, client_memory, media, prompt, sender, store
from iler.engine.pii import PiiMasker, mask_phone_for_log, one_line
from iler.llm import router
from iler.llm.base import ChatMessage, LlmError
from iler.settings_models import NO_COINS_MESSAGE_DEFAULT, BotSettings
from iler.tools import registry
from iler.tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger("iler.engine.turn")

HANDOVER_TOOL = "handover_to_operator"
HANDOVER_REPLY = "Передаю ваш вопрос администратору, он скоро ответит."
AFTER_HOURS_REPEAT = timedelta(hours=12)  # the after-hours text goes to a conversation at most this often
NO_COINS_PAUSE_MINUTES = 24 * 60  # coins used up: minimum operator pause of the conversation


def _not_configured(bot) -> bool:
    """The owner has not written the bot prompt yet (`prompt` field may not exist in older settings)."""
    value = getattr(bot, "prompt", None)
    return value is not None and not str(value).strip()


@dataclass
class TurnOutcome:
    reply_parts: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    handed_over: bool = False
    # LLM spend of this turn (from the provider's usage; 0 when the response had none)
    coins: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


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


def _tz(bot=None) -> tuple[ZoneInfo, str]:
    # reply rules: the time zone of the working hours (bot settings), else the server default
    name = (getattr(bot, "timezone", "") or "").strip() or get_settings().tz
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


def no_coins_message(bot) -> str:
    """The holding reply sent when the coins are used up (bot settings, or the default)."""
    return (getattr(bot, "no_coins_message", "") or "").strip() or NO_COINS_MESSAGE_DEFAULT


async def _handover_no_coins(conv, turn_id: str, *, suspended: bool = False) -> None:
    """The coins are used up (local monthly limit, or the gateway's 402) or the gateway suspended the
    model access (401 key_revoked): the client gets the holding reply, the conversation waits for the
    operator and the admins are told (once per conversation per day).

    The holding reply goes to a conversation once: it is not sent again while it is still the bot's last
    message there, so a client who keeps writing does not get the same phrase over and over. The pause is
    at least a day; the LLM is not called again until the coins are back (the router checks first)."""
    bot = await settings_store.get("bot", BotSettings)
    pause = max(bot.handover_pause_minutes, NO_COINS_PAUSE_MINUTES)
    await store.set_status(conv["id"], "needs_operator", pause_minutes=pause)
    text = no_coins_message(bot)
    parts = sender.split_reply(text)
    prev = await store.last_bot_message(conv["id"])
    if prev is not None and parts and prev["text"] in (parts[-1], text, HANDOVER_REPLY):
        log.info("conversation %s: holding reply already sent", conv["id"])
    else:
        await sender.send_reply(conv["id"], parts, turn_id=turn_id)
    who = conv["name"] or "клиент"
    phone = f" +{conv['phone']}" if conv["phone"] else ""
    if suspended:
        reason = f"Доступ к моделям приостановлен — обратитесь в {brand_company()}."
        key = f"llm-suspended-conv:{conv['id']}:{datetime.now(UTC):%Y-%m-%d}"
    elif gateway_mode():
        reason = f"Коины {brand_name()} закончились: бот не отвечает сам."
        key = f"coins-conv:{conv['id']}:{datetime.now(UTC):%Y-%m-%d}"
    else:
        reason = f"Лимит коинов {brand_name()} исчерпан: бот не отвечает сам."
        key = f"coins-conv:{conv['id']}:{datetime.now(UTC):%Y-%m-%d}"
    await sender.notify_admins(f"{reason} Сообщение ({who}{phone}) ждёт оператора в админке.", key=key)
    log.info("conversation %s handed to the operator: %s", conv["id"],
             "model access suspended" if suspended else "coins used up")


async def _media_reply(conv, decision: media.MediaDecision, bot, turn_id: str) -> TurnOutcome:
    """Voice / photo the bot cannot read: the fixed reply, and the operator when the settings say so."""
    if decision.handover:
        before_status = conv["status"]
        await store.set_status(conv["id"], "needs_operator", pause_minutes=bot.handover_pause_minutes)
        if before_status != "needs_operator":
            name = one_line(conv["name"], 60)
            who = " ".join(x for x in (f"«{name}»" if name else "", f"+{conv['phone']}" if conv["phone"] else "")
                           if x)
            await sender.notify_admins(
                f"Нужен оператор: {who or 'клиент'}. Причина: «{decision.reason}». Посмотрите вложение в админке "
                "или на телефоне и ответьте клиенту.",
                key=f"media:{conv['id']}:{turn_id}",
            )
    parts = sender.split_reply(decision.reply)
    if parts:
        await sender.send_reply(conv["id"], parts, turn_id=turn_id)
    log.info("conversation %s: %s without text, fixed reply%s", conv["id"], decision.kind,
             " and handover" if decision.handover else "")
    return TurnOutcome(reply_parts=parts, handed_over=decision.handover)


async def run_turn(conversation_id: int | None, *, dry_run: bool = False,
                   history: list[ChatMessage] | None = None, phone: str = "") -> TurnOutcome:
    """Run one turn.

    Live (`dry_run=False`): history comes from the DB, admission rules apply, the reply is sent to WhatsApp.
    Dry run (test chat): `history` is used instead of the DB, admission is skipped, nothing is sent or
    written (tools get `ToolContext(dry_run=True)`); an `LlmError` propagates to the caller. Assistant
    messages of that history may carry the tool calls they made: the client card is built from them.
    """
    all_settings = await settings_store.load_all()
    bot = all_settings.bot
    tz, tz_name = _tz(bot)
    now = datetime.now(UTC)
    now_local = now.astimezone(tz)
    conv = None
    client_name = ""
    turn_id = "dry"

    if dry_run:
        chat = list(history or [])
        card = client_memory.card_from_history(chat)  # from the test dialogue's own tool calls
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
        rule = await reply_rules.skip_reason(conv, bot)
        if rule is not None:
            log.info("conversation %s: no reply by the reply rules (%s)", conversation_id, rule)
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
        pending, earlier = media.pending_from_rows(rows)
        media_decision = media.decide(pending, bot, earlier)
        if media_decision is not None:
            return await _media_reply(conv, media_decision, bot, turn_id)
        chat = prompt.history_from_rows(rows)
        try:
            card = await client_memory.load_card(conv, window_start=rows[0]["created_at"], now=now, tz=tz)
        except Exception:  # noqa: BLE001 - the card is a hint: the bot still answers without it
            log.exception("conversation %s: client card failed", conversation_id)
            card = None
        client_name = conv["name"]
        phone = conv["phone"]

    masker = PiiMasker()
    # the card is masked with the same masker as the dialogue (it sits in the system message, which is not)
    messages = prompt.build_messages(bot, chat, now_local, tz_name, client_name,
                                     client_card=masker.mask(client_memory.card_text(card)))
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
    except usage.CoinsExhausted as e:
        if dry_run:
            raise
        await _handover_no_coins(conv, turn_id, suspended=isinstance(e, usage.AccessSuspended))
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
    requests = list(getattr(result, "requests", []) or [])
    outcome = TurnOutcome(
        reply_parts=parts, tool_calls=tool_calls, handed_over=handed_over,
        coins=round(sum(usage.coins_for(r.model or "", r.input_tokens, r.output_tokens,
                                        cache_read=r.cache_read_tokens, cache_write=r.cache_write_tokens)
                        for r in requests), 3),
        input_tokens=sum(r.input_tokens for r in requests),
        output_tokens=sum(r.output_tokens for r in requests),
    )
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
