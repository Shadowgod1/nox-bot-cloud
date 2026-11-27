"""LLM tool `create_booking_request` (spec 5.3): a pending request for the admin to confirm."""

import logging

from iler.settings_models import AllSettings
from iler.tools import registry
from iler.tools.base import ToolContext, ToolResult

# Only the moment is required: a salon or a clinic books a service, a club books seats for some hours.
REQUIRED = ("date", "time")
log = logging.getLogger("iler.tools.booking")


class CreateBookingRequest:
    name = "create_booking_request"
    description = (
        "Создаёт заявку на запись или бронь и отправляет её администратору на подтверждение. Вызывай, когда "
        "клиент назвал дату, время и что ему нужно (услугу, повод или что бронирует). Число мест и длительность "
        "спрашивай, только если бизнес бронирует места или время по часам (клуб, зал, столики); для записи на "
        "услугу их не спрашивай. После вызова скажи клиенту, что заявка передана администратору; запись ещё не "
        "подтверждена."
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Желаемая дата, YYYY-MM-DD"},
            "time": {"type": "string", "description": "Желаемое время начала, HH:MM"},
            "service": {"type": ["string", "null"], "description": "Услуга, повод обращения или что бронирует клиент"},
            "specialist": {"type": ["string", "null"],
                           "description": "Мастер, врач или сотрудник, если клиент выбрал; «любой» — если всё равно"},
            "name": {"type": ["string", "null"], "description": "Имя клиента"},
            "comment": {"type": ["string", "null"], "description": "Пожелания клиента"},
            "duration_hours": {"type": ["number", "null"],
                               "description": "Длительность в часах — только для брони по часам (клуб, зал)"},
            "seats": {"type": ["integer", "null"],
                      "description": "Количество мест (ПК, столиков) — только для брони мест"},
            "zone": {"type": ["string", "null"], "description": "Зона или зал, если клиент указал"},
        },
        "required": list(REQUIRED),
        "additionalProperties": False,
    }

    def enabled(self, settings: AllSettings) -> bool:
        return True

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        missing = [k for k in REQUIRED if args.get(k) in (None, "")]
        if missing:
            return ToolResult(f"error: не хватает полей: {', '.join(missing)} — уточни у клиента")
        payload = {k: args[k] for k in self.parameters["properties"] if args.get(k) not in (None, "")}
        if ctx.dry_run or ctx.conversation_id is None:
            return ToolResult({"status": "simulated", "request_id": 0,
                               # the model must answer exactly as in live mode: a "not created" note made
                               # Haiku tell the client that booking failed
                               "note": "Заявка передана администратору. Скажи клиенту, что администратор подтвердит её."})
        from iler.bookings import service

        try:
            b, created = await service.create_checked(ctx.conversation_id, payload)
        except service.BookingLimit as e:
            return ToolResult(f"error: {e}")
        if payload.get("name"):  # the client card remembers the name the client gave
            from iler.engine import client_memory

            try:
                await client_memory.set_name(client_memory.contact_for(ctx.phone, ctx.extra.get("jid")),
                                             payload["name"])
            except Exception:  # noqa: BLE001 - the card is a convenience; the request is already filed
                log.warning("could not store the client name for conversation %s", ctx.conversation_id)
        return ToolResult({
            "status": "pending",
            "request_id": b.id,
            "note": ("Заявка передана администратору. Скажи клиенту, что администратор подтвердит её."
                     if created else
                     "Такая заявка уже передана администратору и ждёт ответа. Новую не создавай."),
        })


TOOL = registry.register(CreateBookingRequest())
