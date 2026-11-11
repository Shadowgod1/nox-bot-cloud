"""LLM tool `create_booking_request` (spec 5.3): a pending request for the admin to confirm."""

from iler.settings_models import AllSettings
from iler.tools import registry
from iler.tools.base import ToolContext, ToolResult

REQUIRED = ("date", "time", "duration_hours", "seats")


class CreateBookingRequest:
    name = "create_booking_request"
    description = (
        "Создаёт заявку на бронь и отправляет её администратору на подтверждение. Вызывай, когда клиент "
        "назвал дату, время, длительность и число мест. После вызова скажи клиенту, что заявка передана "
        "администратору; бронь ещё не подтверждена."
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Дата брони, YYYY-MM-DD"},
            "time": {"type": "string", "description": "Время начала, HH:MM"},
            "duration_hours": {"type": "number", "description": "Длительность в часах"},
            "seats": {"type": "integer", "description": "Количество мест (ПК)"},
            "zone": {"type": ["string", "null"], "description": "Зона/зал, если клиент указал"},
            "name": {"type": ["string", "null"], "description": "Имя клиента"},
            "comment": {"type": ["string", "null"], "description": "Пожелания клиента"},
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
                               "note": "Тестовый режим: заявка не создана. Скажи клиенту, что заявка передана."})
        from iler.bookings import service

        b = await service.create(ctx.conversation_id, payload)
        return ToolResult({
            "status": "pending",
            "request_id": b.id,
            "note": "Заявка передана администратору. Скажи клиенту, что администратор подтвердит её.",
        })


TOOL = registry.register(CreateBookingRequest())
