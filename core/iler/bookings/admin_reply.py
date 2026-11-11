"""Admin replies to booking requests in WhatsApp: "+12" confirms, "-12 причина" rejects (spec 5.3).

The engine passes every message from an admin chat here and never to the LLM; text that does not match
the pattern is ignored (Review Focus 3).
"""

import logging
import re

from iler.bookings import service
from iler.engine import handler, sender
from iler.whatsapp.base import IncomingMessage

log = logging.getLogger("iler.bookings.admin_reply")

PATTERN = re.compile(r"^[+-]\s*#?(\d+)\s*(.*)$", re.DOTALL)
_STATUS_RU = {"pending": "ожидает", "confirmed": "подтверждена", "rejected": "отклонена", "expired": "истекла"}


def parse(text: str) -> tuple[str, int, str] | None:
    """-> ("confirmed"|"rejected", booking id, note) or None."""
    m = PATTERN.match((text or "").strip())
    if not m:
        return None
    status = "confirmed" if text.strip().startswith("+") else "rejected"
    return status, int(m.group(1)), m.group(2).strip()


async def handle_admin_message(msg: IncomingMessage) -> bool:
    """Returns True when the message was a booking command."""
    parsed = parse(msg.text)
    if parsed is None:
        return False
    status, bid, note = parsed
    by = f"whatsapp:{msg.phone or msg.jid}"
    try:
        b = await service.decide(bid, status, note or None, by)
    except service.BookingNotFound:
        answer = f"Заявка #{bid} не найдена."
    except service.BookingConflict as e:
        answer = f"Заявка #{bid} уже обработана: {_STATUS_RU.get(e.booking.status, e.booking.status)}."
    else:
        answer = (f"Заявка #{b.id} подтверждена, клиенту отправлено сообщение." if status == "confirmed"
                  else f"Заявка #{b.id} отклонена, клиенту отправлено сообщение.")
    await sender.send_to_jid(f"admin-reply:{msg.evo_id}", msg.jid, answer)
    return True


def install() -> None:
    """Register the admin reply handler with the engine (called once from app lifespan)."""
    handler.set_admin_handler(handle_admin_message)
