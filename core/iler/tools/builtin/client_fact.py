"""LLM tool `remember_client_fact`: one lasting fact about the client for the client card."""

from iler.engine import client_memory
from iler.settings_models import AllSettings
from iler.tools import registry
from iler.tools.base import ToolContext, ToolResult


class RememberClientFact:
    name = client_memory.REMEMBER_TOOL
    description = (
        "Запоминает один устойчивый факт о клиенте для следующих разговоров: к какому мастеру ходит, удобное "
        "время, противопоказание или аллергия, как к нему обращаться. Не вызывай для разовых деталей текущей "
        "заявки. Никогда не записывай телефоны, адреса, e-mail и платёжные данные."
    )
    parameters = {
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "Одна короткая строка, например «Ходит к мастеру Айгерим»"},
        },
        "required": ["fact"],
        "additionalProperties": False,
    }

    def enabled(self, settings: AllSettings) -> bool:
        return True

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        try:
            fact = client_memory.clean_fact(args.get("fact"))
        except client_memory.FactRejected as e:
            return ToolResult(f"error: {e}")
        if ctx.dry_run or ctx.conversation_id is None:
            return ToolResult({"status": "simulated", "note": "Тестовый режим: факт не сохранён."})
        contact = client_memory.contact_for(ctx.phone, ctx.extra.get("jid"))
        try:
            status = await client_memory.remember_fact(contact, fact)
        except client_memory.FactRejected as e:
            return ToolResult(f"error: {e}")
        return ToolResult({"status": status, "note": "Факт сохранён." if status == "saved" else "Это уже известно."})


TOOL = registry.register(RememberClientFact())
