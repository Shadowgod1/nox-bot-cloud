"""Booking requests (spec 5.3): created by the LLM tool, decided by an admin (WhatsApp +N/-N or the admin
panel), followed up by the timeout worker. Shared by the API and the WhatsApp admin reply."""

import logging
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from iler import events, settings_store
from iler.db.pool import get_pool
from iler.engine import sender
from iler.settings_models import BotSettings

log = logging.getLogger("iler.bookings")

Status = Literal["pending", "confirmed", "rejected", "expired"]

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


def admin_text(b: BookingRequest) -> str:
    who = " ".join(x for x in (b.name, f"+{b.phone}" if b.phone else "") if x) or "неизвестен"
    return (f"Заявка #{b.id}: {details(b.payload)}. Клиент: {who}. "
            f"Ответьте \"+{b.id}\" чтобы подтвердить или \"-{b.id} причина\" чтобы отклонить.")


async def create(conversation_id: int, payload: dict) -> BookingRequest:
    """Insert a pending request, notify admins in WhatsApp, publish booking.updated."""
    bid = await get_pool().fetchval(
        "INSERT INTO booking_requests(conversation_id, payload) VALUES ($1, $2) RETURNING id",
        conversation_id, payload,
    )
    b = await get(bid)
    await sender.notify_admins(admin_text(b), key=f"booking:{b.id}")
    _publish(b)
    return b


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
