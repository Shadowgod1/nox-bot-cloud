"""Booking requests (spec 5.3): created by the LLM tool, decided by an admin (WhatsApp +N/-N or the admin
panel), followed up by the timeout worker. Shared by the API and the WhatsApp admin reply."""

import logging
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from iler import events, settings_store
from iler.db.pool import get_pool
from iler.engine import sender
from iler.engine.pii import one_line
from iler.settings_models import BotSettings

log = logging.getLogger("iler.bookings")

Status = Literal["pending", "confirmed", "rejected", "expired"]

NAME_MAX = 60
ZONE_MAX = 60
COMMENT_MAX = 300
DUPLICATE_WINDOW_MIN = 30   # the same slot asked again within this window returns the existing request
CONV_WINDOW_MIN = 10        # at most one new pending request per conversation per this window
PHONE_DAILY_MAX = 5         # requests per client phone per 24 h

_COLS = (
    "b.id, b.conversation_id, b.payload, b.status, b.admin_note, b.timeout_notified, b.created_at,"
    " b.decided_at, b.decided_by, c.phone, c.name"
)
_FROM = "FROM booking_requests b JOIN conversations c ON c.id = b.conversation_id"


class BookingRequest(BaseModel):
    id: int
    conversation_id: int
    payload: dict[str, Any]
    status: Status
    admin_note: str | None = None
    timeout_notified: bool = False
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    phone: str = ""
    name: str = ""


class BookingNotFound(Exception):
    pass


class BookingLimit(Exception):
    """The LLM tool may not create another request now; the message is shown to the model."""


class BookingConflict(Exception):
    def __init__(self, booking: BookingRequest):
        super().__init__(f"booking {booking.id} is already {booking.status}")
        self.booking = booking


def details(payload: dict) -> str:
    """Human summary: '2026-11-12 в 18:00, 3 ч, мест: 2, зона VIP, имя Айдар, комментарий: ...'."""
    parts = []
    date, time_ = payload.get("date"), payload.get("time")
    if date or time_:
        parts.append(" в ".join(str(x) for x in (date, time_) if x))
    if payload.get("duration_hours") not in (None, ""):
        parts.append(f"{payload['duration_hours']} ч")
    if payload.get("seats") not in (None, ""):
        parts.append(f"мест: {payload['seats']}")
    if payload.get("zone"):
        parts.append(f"зона {payload['zone']}")
    if payload.get("name"):
        parts.append(f"имя {payload['name']}")
    if payload.get("comment"):
        parts.append(f"комментарий: {payload['comment']}")
    return ", ".join(parts)


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def render(template: str, **values) -> str:
    try:
        return template.format_map(_SafeDict(values)).strip()
    except (ValueError, IndexError, AttributeError):  # malformed template from settings
        return template.strip()


def _publish(b: BookingRequest) -> None:
    events.publish("booking.updated", {"booking": b.model_dump(mode="json")})


async def get(booking_id: int) -> BookingRequest | None:
    row = await get_pool().fetchrow(f"SELECT {_COLS} {_FROM} WHERE b.id = $1", booking_id)
    return BookingRequest(**dict(row)) if row else None


async def list_requests(status: str | None = None, limit: int = 200) -> list[BookingRequest]:
    if status:
        rows = await get_pool().fetch(
            f"SELECT {_COLS} {_FROM} WHERE b.status = $1 ORDER BY b.id DESC LIMIT $2", status, limit
        )
    else:
        rows = await get_pool().fetch(f"SELECT {_COLS} {_FROM} ORDER BY b.id DESC LIMIT $1", limit)
    return [BookingRequest(**dict(r)) for r in rows]


def _format_phone(digits: str) -> str:
    d = "".join(ch for ch in digits or "" if ch.isdigit())
    if len(d) == 11 and d.startswith("7"):
        return f"+7 {d[1:4]} {d[4:7]} {d[7:9]} {d[9:]}"
    return f"+{d}" if d else ""


def admin_text(b: BookingRequest, link: str = "") -> str:
    """Lead notification for the admin (WhatsApp / Telegram). One fact per line; every client-written value is
    one sanitized line in «…», so it can neither add fake lines nor pass for the bot's own wording."""
    p = b.payload
    name = one_line(p.get("name"), NAME_MAX) or one_line(b.name, NAME_MAX)
    lines = [f"Новая заявка #{b.id}"]
    lines.append(f"Имя: «{name}»" if name else "Имя: не указано")
    if b.phone:
        lines.append(f"Телефон: {_format_phone(b.phone)}")
    service = one_line(p.get("service"), ZONE_MAX) or one_line(p.get("zone"), ZONE_MAX)
    if service:
        lines.append(f"Услуга: «{service}»")
    when = []
    date = one_line(p.get("date"), 20)
    time_ = one_line(p.get("time"), 10)
    if date or time_:
        when.append(" в ".join(x for x in (date, time_) if x))
    if p.get("duration_hours") not in (None, ""):
        when.append(f"{one_line(p['duration_hours'], 10)} ч")
    if p.get("seats") not in (None, ""):
        when.append(f"мест: {one_line(p['seats'], 10)}")
    if when:
        lines.append(f"Когда: {', '.join(when)}")
    comment = one_line(p.get("comment"), COMMENT_MAX)
    if comment:
        lines.append(f"Комментарий: «{comment}»")
    if link:
        lines.append(f"Диалог: {link}")
    lines.append(f"Ответьте \"+{b.id}\" — подтвердить, \"-{b.id} причина\" — отклонить.")
    return "\n".join(lines)


def clean_payload(payload: dict) -> dict:
    """Free-text fields from the model/client: one line, length-limited."""
    out = dict(payload)
    for key, limit in (("name", NAME_MAX), ("zone", ZONE_MAX), ("comment", COMMENT_MAX)):
        if key in out and out[key] is not None:
            value = one_line(out[key], limit)
            if value:
                out[key] = value
            else:
                del out[key]
    return out


async def _announce(b: BookingRequest) -> None:
    from iler.notify import conversation_link

    await sender.notify_admins(admin_text(b, await conversation_link(b.conversation_id)), key=f"booking:{b.id}")
    _publish(b)


async def create(conversation_id: int, payload: dict) -> BookingRequest:
    """Insert a pending request, notify admins in WhatsApp, publish booking.updated."""
    bid = await get_pool().fetchval(
        "INSERT INTO booking_requests(conversation_id, payload) VALUES ($1, $2) RETURNING id",
        conversation_id, payload,
    )
    b = await get(bid)
    await _announce(b)
    return b


def _same_slot(a: dict, b: dict) -> bool:
    return all(str(a.get(k) or "").strip() == str(b.get(k) or "").strip() for k in ("date", "time")) and \
        _num(a.get("seats")) == _num(b.get("seats"))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v or "").strip()


async def create_checked(conversation_id: int, payload: dict) -> tuple[BookingRequest, bool]:
    """`create` for the LLM tool, with anti-duplicate and anti-spam rules. Returns (request, created):
    the same slot (date+time+seats) still pending from the last DUPLICATE_WINDOW_MIN minutes is returned
    as is. Raises BookingLimit when the conversation already has a pending request from the last
    CONV_WINDOW_MIN minutes, or the client's phone made PHONE_DAILY_MAX requests in the last 24 h."""
    payload = clean_payload(payload)
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # serialize concurrent tool calls of one conversation
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"booking:{conversation_id}")
        recent = await conn.fetch(
            "SELECT id, payload, created_at > now() - $2::interval AS in_conv_window FROM booking_requests"
            " WHERE conversation_id = $1 AND status = 'pending' AND created_at > now() - $3::interval"
            " ORDER BY id DESC",
            conversation_id, timedelta(minutes=CONV_WINDOW_MIN), timedelta(minutes=DUPLICATE_WINDOW_MIN),
        )
        for r in recent:
            if _same_slot(r["payload"], payload):
                existing = r["id"]
                break
        else:
            existing = None
            for r in recent:
                if r["in_conv_window"]:
                    raise BookingLimit(f"уже есть заявка #{r['id']}, дождитесь ответа администратора")
            phone = await conn.fetchval("SELECT phone FROM conversations WHERE id = $1", conversation_id)
            if phone:
                today = await conn.fetchval(
                    "SELECT count(*) FROM booking_requests b JOIN conversations c ON c.id = b.conversation_id"
                    " WHERE c.phone = $1 AND b.created_at > now() - interval '1 day'", phone,
                )
            else:
                today = await conn.fetchval(
                    "SELECT count(*) FROM booking_requests WHERE conversation_id = $1"
                    " AND created_at > now() - interval '1 day'", conversation_id,
                )
            if today >= PHONE_DAILY_MAX:
                raise BookingLimit("лимит заявок на сегодня исчерпан, предложи клиенту дождаться администратора")
            bid = await conn.fetchval(
                "INSERT INTO booking_requests(conversation_id, payload) VALUES ($1, $2) RETURNING id",
                conversation_id, payload,
            )
    if existing is not None:
        return await get(existing), False
    b = await get(bid)
    await _announce(b)
    return b, True


async def decide(booking_id: int, status: Literal["confirmed", "rejected"], note: str | None,
                 by: str) -> BookingRequest:
    """Confirm or reject a pending request and message the client. Raises BookingNotFound / BookingConflict."""
    if status not in ("confirmed", "rejected"):
        raise ValueError(status)
    note = (note or "").strip() or None
    updated = await get_pool().fetchval(
        "UPDATE booking_requests SET status = $2, admin_note = $3, decided_at = now(), decided_by = $4"
        " WHERE id = $1 AND status = 'pending' RETURNING id",
        booking_id, status, note, by,
    )
    b = await get(booking_id)
    if b is None:
        raise BookingNotFound(booking_id)
    if updated is None:
        raise BookingConflict(b)

    bot = await settings_store.get("bot", BotSettings)
    if status == "confirmed":
        text = render(bot.booking_confirm_template, details=details(b.payload), note=note or "")
        if note and "{note}" not in bot.booking_confirm_template:
            text = f"{text}\n{note}"
    else:
        text = render(bot.booking_reject_template, reason=note or "администратор отклонил заявку",
                      details=details(b.payload))
    try:
        await sender.send_to_conversation(b.conversation_id, text, f"booking:{b.id}:{status}")
    except Exception:  # noqa: BLE001 - the decision stands; the operator sees the chat in the admin panel
        log.exception("could not message the client about booking %s", b.id)
    _publish(b)
    return b


async def notify_timeouts(timeout_min: int, message: str) -> int:
    """Pending requests older than `timeout_min` get one follow-up to the client and the admin-panel flag."""
    pool = get_pool()
    rows = await pool.fetch(
        "UPDATE booking_requests SET timeout_notified = true"
        " WHERE status = 'pending' AND NOT timeout_notified AND created_at < now() - $1::interval"
        " RETURNING id",
        timedelta(minutes=timeout_min),
    )
    for r in rows:
        b = await get(r["id"])
        if b is None:
            continue
        if message.strip():
            try:
                await sender.send_to_conversation(b.conversation_id, message.strip(), f"booking:{b.id}:timeout")
            except Exception:  # noqa: BLE001
                log.exception("timeout follow-up failed for booking %s", b.id)
        _publish(b)
    return len(rows)
