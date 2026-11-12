"""ILER coins: the client's LLM spend in ILER's own unit, with a monthly limit set by ILER.

1 coin = $0.001 of model cost. Spend is computed from ``ai_requests`` (tokens per model) with a price
table; the month and its days follow the club's time zone (ILER_TZ). The limit (ILER_MONTHLY_COINS) and
price overrides (ILER_MODEL_PRICES, JSON ``{"model-prefix": [input_usd_per_mtok, output_usd_per_mtok]}``)
are set by the ILER technician in .env; 0 means no limit.

When the limit is reached the bot stops answering by itself and hands every new conversation to the
operator; admins get a WhatsApp alert at 80% and at 100% (once per month each).
"""

import calendar
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from iler import settings_store
from iler.config import get_settings
from iler.db.pool import get_pool

log = logging.getLogger("iler.usage")

COIN_USD = 0.001
# $ per 1M tokens (input, output); longest matching prefix wins. Unknown models use FALLBACK_PRICE,
# a deliberately high price so an unpriced model never looks cheaper than it is.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
FALLBACK_PRICE = (2.0, 10.0)
ALERT_LEVELS = (80, 100)
_CACHE_S = 30.0


class CoinsExhausted(Exception):
    """The monthly coin limit is used up (raised for the test chat)."""


class UsageAlerts(BaseModel):
    month: str = ""  # "2026-11"
    sent: list[int] = []


def prices() -> dict[str, tuple[float, float]]:
    table = dict(DEFAULT_PRICES)
    raw = get_settings().model_prices.strip()
    if raw:
        try:
            for prefix, pair in json.loads(raw).items():
                table[str(prefix)] = (float(pair[0]), float(pair[1]))
        except (ValueError, TypeError, IndexError, AttributeError):
            log.error("ILER_MODEL_PRICES is not valid JSON {\"model\": [in, out]}; using the defaults")
    return table


def price_for(model: str, table: dict[str, tuple[float, float]] | None = None) -> tuple[float, float]:
    table = table if table is not None else prices()
    match = max((p for p in table if model.startswith(p)), key=len, default=None)
    return table[match] if match else FALLBACK_PRICE


def coins_for(model: str, input_tokens: int, output_tokens: int,
              table: dict[str, tuple[float, float]] | None = None) -> float:
    pin, pout = price_for(model, table)
    return (input_tokens * pin + output_tokens * pout) / 1_000_000 / COIN_USD


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().tz)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def month_start(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass
class MonthUsage:
    limit: int  # 0 = no limit
    spent: float
    days: list[tuple[date, float]]  # every day of the month so far (zeros included)
    by_model: list[tuple[str, float]]
    avg_per_day: float
    projected: float  # spend by the end of the month at the current pace
    runs_out_on: date | None  # day the limit is reached at the current pace (None: lasts the month)
    days_in_month: int
    alerts: list[int] = field(default_factory=list)

    @property
    def remaining(self) -> float | None:
        return max(self.limit - self.spent, 0.0) if self.limit else None

    @property
    def percent(self) -> float | None:
        return round(self.spent / self.limit * 100, 1) if self.limit else None

    @property
    def exhausted(self) -> bool:
        return bool(self.limit) and self.spent >= self.limit


async def month_usage(now: datetime | None = None) -> MonthUsage:
    now = now or datetime.now(UTC)
    tz = _tz()
    start = month_start(now, tz)
    local_today = now.astimezone(tz).date()
    dim = calendar.monthrange(start.year, start.month)[1]
    rows = await get_pool().fetch(
        "SELECT (created_at AT TIME ZONE $2)::date AS day, model,"
        " sum(input_tokens)::bigint AS tin, sum(output_tokens)::bigint AS tout"
        " FROM ai_requests WHERE created_at >= $1 GROUP BY 1, 2",
        start.astimezone(UTC), str(tz),
    )
    table = prices()
    per_day: dict[date, float] = {}
    per_model: dict[str, float] = {}
    for r in rows:
        c = coins_for(r["model"], r["tin"], r["tout"], table)
        per_day[r["day"]] = per_day.get(r["day"], 0.0) + c
        per_model[r["model"]] = per_model.get(r["model"], 0.0) + c
    days = [(start.date() + timedelta(days=i), round(per_day.get(start.date() + timedelta(days=i), 0.0), 2))
            for i in range((local_today - start.date()).days + 1)]
    spent = sum(per_day.values())
    # elapsed part of the month in days (today counts as the fraction already lived)
    elapsed = max((now - start.astimezone(UTC)).total_seconds() / 86400, 1 / 24)
    avg = spent / elapsed
    limit = max(get_settings().monthly_coins, 0)
    runs_out = None
    if limit and avg > 0:
        when = start + timedelta(days=limit / avg)
        if when.date() <= start.date() + timedelta(days=dim - 1):
            runs_out = max(when.date(), local_today) if spent < limit else local_today
    alerts = await settings_store.get("usage_alerts", UsageAlerts)
    return MonthUsage(
        limit=limit,
        spent=round(spent, 2),
        days=days,
        by_model=sorted(((m, round(c, 2)) for m, c in per_model.items()), key=lambda x: -x[1]),
        avg_per_day=round(avg, 2),
        projected=round(avg * dim, 2),
        runs_out_on=runs_out,
        days_in_month=dim,
        alerts=alerts.sent if alerts.month == f"{start:%Y-%m}" else [],
    )


_cache: tuple[float, bool] | None = None


def invalidate() -> None:
    global _cache
    _cache = None


async def limit_reached() -> bool:
    """Cheap check before each bot turn (cached for a few seconds)."""
    global _cache
    if get_settings().monthly_coins <= 0:
        return False
    if _cache and time.monotonic() - _cache[0] < _CACHE_S:
        return _cache[1]
    reached = (await month_usage()).exhausted
    _cache = (time.monotonic(), reached)
    return reached


async def after_spend() -> None:
    """Called after an LLM turn: refresh the cache and send the 80% / 100% alerts once per month."""
    invalidate()
    if get_settings().monthly_coins <= 0:
        return
    try:
        usage = await month_usage()
        month = f"{month_start(datetime.now(UTC), _tz()):%Y-%m}"
        state = await settings_store.get("usage_alerts", UsageAlerts)
        if state.month != month:
            state = UsageAlerts(month=month)
        due = [lvl for lvl in ALERT_LEVELS if (usage.percent or 0) >= lvl and lvl not in state.sent]
        if not due:
            return
        level = max(due)
        state.sent = sorted(set(state.sent) | set(due))
        await settings_store.put("usage_alerts", state)
        from iler.engine import sender

        if level >= 100:
            text = (f"ILER Bot: лимит коинов на месяц исчерпан ({usage.spent:.0f} из {usage.limit}). "
                    "Бот больше не отвечает сам: новые сообщения ждут оператора в админке. "
                    "Чтобы увеличить лимит, обратитесь в ILER.")
        else:
            out = f" При текущем темпе лимит закончится {usage.runs_out_on:%d.%m}." if usage.runs_out_on else ""
            text = f"ILER Bot: израсходовано {usage.percent:.0f}% коинов на месяц ({usage.spent:.0f} из {usage.limit}).{out}"
        await sender.notify_admins(text, key=f"coins:{month}:{level}")
    except Exception:  # noqa: BLE001 - accounting must never break a reply
        log.exception("coin usage check failed")
