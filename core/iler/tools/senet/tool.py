"""LLM tool `senet_free_workstations` (fail-open)."""

import logging

from iler import settings_store
from iler.settings_models import AllSettings, SenetSettings
from iler.tools import registry
from iler.tools.base import ToolContext, ToolResult
from iler.tools.senet.client import SenetClient, SenetError
from iler.tools.senet.mapping import to_workstation

log = logging.getLogger("iler.senet")

UNAVAILABLE = "error: SENET недоступен, предложи оформить заявку через create_booking_request"
NO_ZONE = "без зоны"


def is_configured(s: SenetSettings) -> bool:
    return bool(s.enabled and s.orgname and s.app_key and s.app_key.get_secret_value())


def make_client(s: SenetSettings) -> SenetClient:
    return SenetClient(s.orgname or "", s.app_key.get_secret_value() if s.app_key else "")


class SenetFreeWorkstations:
    name = "senet_free_workstations"
    description = (
        "Показывает, какие компьютеры в клубе свободны прямо сейчас (данные из SENET). "
        "Используй, когда клиент спрашивает о свободных местах. Можно отфильтровать по зоне."
    )
    parameters = {
        "type": "object",
        "properties": {
            "zone": {
                "type": ["string", "null"],
                "description": "Название зоны (например «VIP» или «Общий зал»); пусто — все зоны.",
            }
        },
        "additionalProperties": False,
    }

    def enabled(self, settings: AllSettings) -> bool:
        return is_configured(settings.senet)

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        try:
            senet = ctx.extra.get("senet")
            if not isinstance(senet, SenetSettings):
                senet = await settings_store.get("senet", SenetSettings)
            if not is_configured(senet):
                return ToolResult(UNAVAILABLE)
            raw = await make_client(senet).list_workstations()
        except SenetError as e:
            log.warning("SENET request failed: %s", e)
            return ToolResult(UNAVAILABLE)
        except Exception:  # noqa: BLE001 - fail-open, the turn must not break
            log.exception("SENET tool failed")
            return ToolResult(UNAVAILABLE)

        zone_filter = (args.get("zone") or "").strip().casefold() if isinstance(args.get("zone"), str) else ""
        free: list[dict] = []
        by_zone: dict[str, int] = {}
        for item in raw:
            ws = to_workstation(item)
            if ws.state != "free":
                continue
            zone = ws.zone or NO_ZONE
            if zone_filter and zone_filter not in zone.casefold():
                continue
            free.append({"name": ws.name, "zone": zone})
            by_zone[zone] = by_zone.get(zone, 0) + 1
        return ToolResult({"free": free, "total_free": len(free), "by_zone": by_zone})


TOOL = registry.register(SenetFreeWorkstations())
