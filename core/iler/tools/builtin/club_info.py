"""LLM tool `get_club_info` (spec 5.3): profile sections on demand, so the prompt stays small."""

from iler.settings_models import AllSettings, BotSettings
from iler.tools import registry
from iler.tools.base import ToolContext, ToolResult

_ALIASES = {
    "services": "services", "prices": "services", "price": "services", "цены": "services", "услуги": "services",
    "тарифы": "services",
    "address": "address", "адрес": "address", "location": "address",
    "hours": "hours", "часы": "hours", "schedule": "hours", "график": "hours",
    "faq": "faq", "вопросы": "faq",
}


def _hours(bot: BotSettings) -> dict:
    return bot.hours.model_dump()


class GetClubInfo:
    name = "get_club_info"
    description = (
        "Возвращает раздел профиля заведения: services (цены и услуги), address, hours, faq. "
        "Для других тем (железо, акции, парковка и т. п.) передай ключевое слово — вернутся подходящие записи."
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "services | address | hours | faq | ключевое слово"},
        },
        "required": ["topic"],
        "additionalProperties": False,
    }

    def enabled(self, settings: AllSettings) -> bool:
        return True

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        bot = ctx.settings
        topic = str(args.get("topic") or "").strip()
        section = _ALIASES.get(topic.casefold())
        if section == "services":
            return ToolResult({"section": "services", "items": [s.model_dump() for s in bot.services]})
        if section == "address":
            return ToolResult({"section": "address", "address": bot.address})
        if section == "hours":
            return ToolResult({"section": "hours", "hours": _hours(bot)})
        if section == "faq":
            return ToolResult({"section": "faq", "items": [f.model_dump() for f in bot.faq]})
        needle = topic.casefold()
        matches = [f.model_dump() for f in bot.faq if needle and (needle in f.q.casefold() or needle in f.a.casefold())]
        services = [s.model_dump() for s in bot.services
                    if needle and (needle in s.name.casefold() or needle in s.note.casefold())]
        if not matches and not services and needle and needle in bot.instructions.casefold():
            return ToolResult({"section": "search", "matches": [], "instructions": bot.instructions})
        result: dict = {"section": "search", "matches": matches}
        if services:
            result["services"] = services
        if not matches and not services:
            result["note"] = "Сведений нет в профиле. Не выдумывай; предложи уточнить у администратора."
        return ToolResult(result)


TOOL = registry.register(GetClubInfo())
