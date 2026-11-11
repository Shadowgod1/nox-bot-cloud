"""Engine entry point: `handle_incoming` is the inbound handler (spec 5.1 step 2).

- Admin chats (admin phones / admin group) never reach the LLM: the admin handler (+N/-N parser,
  registered by `iler.bookings.admin_reply`) gets them; anything else from admins is ignored.
- `fromMe` messages: the bot's own echo is ignored; anything else is an operator replying from the phone,
  which pauses the bot for `handover_pause_minutes`.
- Client messages are persisted before returning (the inbound event is marked processed when the handler
  returns) and a turn is scheduled through the merge buffer.
- `install()` also schedules a startup recovery for client messages left unanswered by a restart.
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


def set_admin_handler(fn: AdminHandler | None) -> None:
    global _admin_handler
    _admin_handler = fn


def get_admin_handler() -> AdminHandler | None:
    return _admin_handler


async def _run_turn_safe(conversation_id: int) -> None:
    try:
        await turn.run_turn(conversation_id)
    except Exception:  # noqa: BLE001 - background task
        log.exception("turn failed for conversation %s", conversation_id)


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


async def recover_unanswered() -> int:
    """Schedule turns for conversations whose last message is a fresh, unanswered client message (the
    process restarted while it sat in the merge buffer). Returns how many were scheduled."""
    rows = await get_pool().fetch(
        "SELECT c.id, c.chat_jid FROM conversations c"
        " JOIN LATERAL (SELECT author, created_at FROM messages m WHERE m.conversation_id = c.id"
        "               ORDER BY m.id DESC LIMIT 1) last ON true"
        " WHERE last.author = 'client'"
        f"   AND last.created_at > now() - interval '{RECOVERY_WINDOW_MINUTES} minutes'"
        "   AND c.status <> 'closed'"
        "   AND NOT (c.status IN ('paused', 'needs_operator') AND (c.paused_until IS NULL OR c.paused_until > now()))"
    )
    for r in rows:
        schedule_turn(r["chat_jid"], r["id"], 0)
    if rows:
        log.info("startup recovery: %d unanswered conversation(s) scheduled", len(rows))
    return len(rows)


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
