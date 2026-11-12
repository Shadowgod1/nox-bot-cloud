"""Persistence of conversations/messages for the engine, plus their JSON shape for SSE events."""

from datetime import datetime, timedelta

from iler import events
from iler.db.pool import get_pool

CONV_COLS = "id, chat_jid, phone, name, status, paused_until, last_message_at, unread, created_at"
MSG_COLS = "id, conversation_id, direction, author, text, media, evo_message_id, created_at"


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def conv_dict(row) -> dict:
    return {k: _iso(row[k]) for k in ("id", "chat_jid", "phone", "name", "status", "paused_until",
                                      "last_message_at", "unread", "created_at")}


def msg_dict(row) -> dict:
    return {k: _iso(row[k]) for k in ("id", "conversation_id", "direction", "author", "text", "media",
                                      "evo_message_id", "created_at")}


async def get_conversation(conversation_id: int):
    return await get_pool().fetchrow(f"SELECT {CONV_COLS} FROM conversations WHERE id = $1", conversation_id)


async def get_conversation_by_jid(jid: str):
    return await get_pool().fetchrow(f"SELECT {CONV_COLS} FROM conversations WHERE chat_jid = $1", jid)


async def upsert_conversation(jid: str, phone: str, name: str, *, client_message: bool):
    """Create or touch the conversation. A client message bumps `unread`, reopens a closed chat and lifts an
    expired pause."""
    return await get_pool().fetchrow(
        "INSERT INTO conversations(chat_jid, phone, name, last_message_at, unread)"
        " VALUES ($1, $2, $3, now(), $4)"
        " ON CONFLICT (chat_jid) DO UPDATE SET"
        "   last_message_at = now(),"
        "   name = CASE WHEN EXCLUDED.name <> '' THEN EXCLUDED.name ELSE conversations.name END,"
        "   phone = CASE WHEN EXCLUDED.phone <> '' THEN EXCLUDED.phone ELSE conversations.phone END,"
        "   unread = conversations.unread + EXCLUDED.unread,"
        "   status = CASE"
        "     WHEN NOT $5 THEN conversations.status"
        "     WHEN conversations.status = 'closed' THEN 'active'"
        "     WHEN conversations.status IN ('paused', 'needs_operator')"
        "          AND conversations.paused_until IS NOT NULL AND conversations.paused_until <= now() THEN 'active'"
        "     ELSE conversations.status END,"
        "   paused_until = CASE"
        "     WHEN $5 AND conversations.paused_until IS NOT NULL AND conversations.paused_until <= now() THEN NULL"
        "     ELSE conversations.paused_until END"
        f" RETURNING {CONV_COLS}",
        jid, phone or "", name or "", 1 if client_message else 0, client_message,
    )


async def insert_message(conversation_id: int, direction: str, author: str, text: str, *,
                         media: dict | None = None, evo_message_id: str | None = None, publish: bool = True):
    pool = get_pool()
    row = await pool.fetchrow(
        "INSERT INTO messages(conversation_id, direction, author, text, media, evo_message_id)"
        f" VALUES ($1, $2, $3, $4, $5, $6) RETURNING {MSG_COLS}",
        conversation_id, direction, author, text or "", media, evo_message_id,
    )
    if direction == "out":
        await pool.execute("UPDATE conversations SET last_message_at = now() WHERE id = $1", conversation_id)
    if publish:
        events.publish("message.new", {"conversation_id": conversation_id, "message": msg_dict(row)})
    return row


async def message_exists(evo_message_id: str) -> bool:
    return bool(await get_pool().fetchval(
        "SELECT 1 FROM messages WHERE evo_message_id = $1 LIMIT 1", evo_message_id
    ))


# when the conversation (last) got needs_operator: the dashboard counts today's handovers from it
_NEEDS_OP_AT = "needs_operator_at = CASE WHEN $2 = 'needs_operator' THEN now() ELSE needs_operator_at END"


async def set_status(conversation_id: int, status: str, *, pause_minutes: int | None = None,
                     clear_pause: bool = False):
    """Change status (optionally pausing for N minutes); publishes conversation.updated."""
    pool = get_pool()
    if pause_minutes is not None:
        row = await pool.fetchrow(
            f"UPDATE conversations SET status = $2, {_NEEDS_OP_AT}, paused_until = now() + $3::interval"
            f" WHERE id = $1 RETURNING {CONV_COLS}",
            conversation_id, status, timedelta(minutes=pause_minutes),
        )
    elif clear_pause:
        row = await pool.fetchrow(
            f"UPDATE conversations SET status = $2, {_NEEDS_OP_AT}, paused_until = NULL"
            f" WHERE id = $1 RETURNING {CONV_COLS}",
            conversation_id, status,
        )
    else:
        row = await pool.fetchrow(
            f"UPDATE conversations SET status = $2, {_NEEDS_OP_AT} WHERE id = $1 RETURNING {CONV_COLS}",
            conversation_id, status,
        )
    if row is not None:
        publish_conversation(row)
    return row


def publish_conversation(row) -> None:
    events.publish("conversation.updated", {"conversation": conv_dict(row)})


async def has_client_message_after(conversation_id: int, message_id: int) -> bool:
    """A client message newer than `message_id` exists (it arrived while a turn was running)."""
    return bool(await get_pool().fetchval(
        "SELECT 1 FROM messages WHERE conversation_id = $1 AND author = 'client' AND id > $2 LIMIT 1",
        conversation_id, message_id,
    ))


async def last_bot_message(conversation_id: int):
    return await get_pool().fetchrow(
        f"SELECT {MSG_COLS} FROM messages WHERE conversation_id = $1 AND author = 'bot' ORDER BY id DESC LIMIT 1",
        conversation_id,
    )


async def recent_messages(conversation_id: int, limit: int) -> list:
    rows = await get_pool().fetch(
        f"SELECT {MSG_COLS} FROM messages WHERE conversation_id = $1 ORDER BY id DESC LIMIT $2",
        conversation_id, limit,
    )
    return list(reversed(rows))
