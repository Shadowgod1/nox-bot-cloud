"""Engine entry point: `handle_incoming` is the inbound handler (spec 5.1 step 2).

- Admin chats (admin phones / admin group) never reach the LLM: the admin handler (+N/-N parser,
  registered by `iler.bookings.admin_reply`) gets them; anything else from admins is ignored.
- `fromMe` messages: the bot's own echo is ignored; anything else is an operator replying from the phone,
  which pauses the bot for `handover_pause_minutes`.
- Client messages are persisted before returning (the inbound event is marked processed when the handler
  returns) and a turn is scheduled through the merge buffer.
- `install()` also schedules a startup recovery for client messages left unanswered by a restart;
  `sweep_unanswered()` (run every 60 s by the outbound reconcile worker) catches turns that failed later.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from iler import inbound, settings_store
from iler.db.pool import get_pool
from iler.engine import admission, buffer, store, turn
from iler.engine.pii import mask_phone_for_log
from iler.settings_models import BotSettings, NotifySettings
from iler.whatsapp.base import IncomingMessage

log = logging.getLogger("iler.engine.handler")

AdminHandler = Callable[[IncomingMessage], Awaitable[object]]
_admin_handler: AdminHandler | None = None
_background: set[asyncio.Task] = set()

ECHO_WINDOW_MINUTES = 10
RECOVERY_WINDOW_MINUTES = 15
TURN_ATTEMPTS = 3
TURN_RETRY_DELAYS = (2.0, 8.0)       # seconds before attempt 2 and 3 (scaled by buffer.SCALE in tests)
SWEEP_MIN_AGE_SECONDS = 30           # younger messages are still in (or about to enter) the merge buffer
MAX_SWEEPS_PER_MESSAGE = 3           # a message whose turn keeps failing is left to the operator after that
_sweeps: dict[int, tuple[int, int]] = {}  # conversation id -> (last client message id, sweeps scheduled)


def set_admin_handler(fn: AdminHandler | None) -> None:
    global _admin_handler
    _admin_handler = fn


def get_admin_handler() -> AdminHandler | None:
    return _admin_handler


async def _run_turn_safe(conversation_id: int) -> None:
    """Run the turn, retrying an unexpected exception with backoff (`LlmError` is handled inside the turn).
    Retries are safe: the turn id is the last client message id, so parts already sent are never resent.
    The retries keep the chat's buffer slot, so a message arriving meanwhile queues behind them."""
    for attempt in range(1, TURN_ATTEMPTS + 1):
        try:
            await turn.run_turn(conversation_id)
            return
        except Exception:  # noqa: BLE001 - background task
            if attempt == TURN_ATTEMPTS:
                log.exception("turn failed for conversation %s (attempt %d, giving up)", conversation_id, attempt)
                return
            log.exception("turn failed for conversation %s (attempt %d), retrying", conversation_id, attempt)
            await asyncio.sleep(TURN_RETRY_DELAYS[attempt - 1] * buffer.SCALE)


def schedule_turn(jid: str, conversation_id: int, delay_seconds: float) -> None:
    buffer.schedule(jid, delay_seconds, lambda: _run_turn_safe(conversation_id))


async def _is_bot_echo(msg: IncomingMessage) -> bool:
    """Our own sends come back as fromMe. Match by Evolution id, or (the echo may beat the send's response)
    by the same text sent/being sent to this chat recently."""
    pool = get_pool()
    if await pool.fetchval("SELECT 1 FROM outbound_intents WHERE evo_message_id = $1 LIMIT 1", msg.evo_id):
        return True
    return bool(await pool.fetchval(
        "SELECT 1 FROM outbound_intents WHERE chat_jid = $1 AND text = $2"
        " AND status IN ('sending', 'sent', 'uncertain')"
        f" AND created_at > now() - interval '{ECHO_WINDOW_MINUTES} minutes' LIMIT 1",
        msg.jid, msg.text,
    ))


async def _handle_operator_reply(msg: IncomingMessage) -> None:
    if await store.message_exists(msg.evo_id) or await _is_bot_echo(msg):
        return
    bot = await settings_store.get("bot", BotSettings)
    conv = await store.upsert_conversation(msg.jid, msg.phone, "", client_message=False)
    await store.insert_message(conv["id"], "out", "operator", msg.text, media=msg.media, evo_message_id=msg.evo_id)
    await store.set_status(conv["id"], "paused", pause_minutes=bot.handover_pause_minutes)
    log.info("operator replied from the phone in conversation %s; bot paused", conv["id"])


async def handle_incoming(msg: IncomingMessage) -> None:
    notify = await settings_store.get("notify", NotifySettings)

    if admission.is_admin_chat(msg, notify):
        if not msg.from_me and _admin_handler is not None:
            await _admin_handler(msg)
        return  # admin chats never reach the LLM
    if msg.is_group:
        return  # only the admin group is let through by the dispatcher

    if msg.from_me:
        await _handle_operator_reply(msg)
        return

    duplicate = await store.message_exists(msg.evo_id)
    conv = await store.upsert_conversation(msg.jid, msg.phone, msg.push_name, client_message=not duplicate)
    if not duplicate:
        await store.insert_message(conv["id"], "in", "client", msg.text, media=msg.media, evo_message_id=msg.evo_id)
        store.publish_conversation(conv)
    bot = await settings_store.get("bot", BotSettings)
    log.debug("client message in conversation %s from %s", conv["id"], mask_phone_for_log(msg.phone))
    schedule_turn(msg.jid, conv["id"], bot.buffer_delay_seconds)


async def _unanswered(min_age_seconds: float) -> list:
    return await get_pool().fetch(
        "SELECT c.id, c.chat_jid, last.id AS message_id FROM conversations c"
        " JOIN LATERAL (SELECT id, author, created_at FROM messages m WHERE m.conversation_id = c.id"
        "               ORDER BY m.id DESC LIMIT 1) last ON true"
        " WHERE last.author = 'client'"
        f"   AND last.created_at > now() - interval '{RECOVERY_WINDOW_MINUTES} minutes'"
        "   AND last.created_at <= now() - $1::float8 * interval '1 second'"
        "   AND c.status <> 'closed'"
        "   AND NOT (c.status IN ('paused', 'needs_operator') AND (c.paused_until IS NULL OR c.paused_until > now()))",
        float(min_age_seconds),
    )


async def recover_unanswered(*, min_age_seconds: float = 0) -> int:
    """Schedule turns for conversations whose last message is a fresh, unanswered client message (the
    process restarted while it sat in the merge buffer, or its turn failed). Chats with a buffered or running
    turn are skipped. Returns how many were scheduled."""
    rows = [r for r in await _unanswered(min_age_seconds) if not buffer.pending(r["chat_jid"])]
    for r in rows:
        schedule_turn(r["chat_jid"], r["id"], 0)
    if rows:
        log.info("recovery: %d unanswered conversation(s) scheduled", len(rows))
    return len(rows)


async def sweep_unanswered() -> int:
    """Periodic safety net (outbound reconcile worker, every 60 s): re-run turns for client messages left
    unanswered, e.g. after a turn failed all its retries. Each message is swept at most
    MAX_SWEEPS_PER_MESSAGE times. Double sends are impossible: the turn id is the client message id."""
    rows = [r for r in await _unanswered(SWEEP_MIN_AGE_SECONDS) if not buffer.pending(r["chat_jid"])]
    live = {r["id"] for r in rows}
    for cid in [c for c in _sweeps if c not in live]:
        del _sweeps[cid]
    scheduled = 0
    for r in rows:
        mid, count = _sweeps.get(r["id"], (r["message_id"], 0))
        if mid != r["message_id"]:
            count = 0
        if count >= MAX_SWEEPS_PER_MESSAGE:
            continue
        _sweeps[r["id"]] = (r["message_id"], count + 1)
        schedule_turn(r["chat_jid"], r["id"], 0)
        scheduled += 1
    if scheduled:
        log.info("sweeper: %d unanswered conversation(s) scheduled", scheduled)
    return scheduled


async def _recover_safe() -> None:
    try:
        await recover_unanswered()
    except Exception:  # noqa: BLE001
        log.exception("startup recovery failed")


def install() -> None:
    """Register the inbound message handler and schedule the startup recovery (called from app lifespan)."""
    inbound.set_handler(handle_incoming)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_recover_safe(), name="engine:recovery")
    _background.add(task)
    task.add_done_callback(_background.discard)
