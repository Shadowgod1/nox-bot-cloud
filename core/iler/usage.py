"""ILER coins: the client's LLM spend in ILER's own unit, with a monthly limit set by ILER.

1 coin = $0.001 of model cost. Spend is computed from ``ai_requests`` (tokens per model) with a price
table; the month and its days follow the club's time zone (ILER_TZ). The limit (ILER_MONTHLY_COINS) and
price overrides (ILER_MODEL_PRICES, JSON ``{"model-prefix": [input_usd_per_mtok, output_usd_per_mtok]}``)
are set by the ILER technician in .env; 0 means no limit.

When the limit is reached the bot stops answering by itself and hands every new conversation to the
operator; admins get a WhatsApp alert at 80%, 95% and 100% spent (20% and 5% left, and none; once per
month and limit each; a level is marked as sent only after WhatsApp took the message, so a failed alert
is retried on the next turn).

LLM gateway mode (ILER_LLM_GATEWAY_URL, see ``iler.llm.gateway``): the gateway is the authority on coins.
Local spend is still computed (per-model and per-day stats), but the local limit never blocks; the
alerts follow the gateway balance (``coins_left`` of ``monthly_limit`` for the period ending
``period_end``).

Prices: a model with no entry in the price table (DEFAULT_PRICES + ILER_MODEL_PRICES) is billed at
FALLBACK_PRICE, the highest tariff, so it never looks cheaper than it is. Such models are listed in
``MonthUsage.unpriced_models`` and the dashboard shows a warning so the technician can add a price.

Forecast: the pace is measured from the later of the month start and the first LLM request of the month
(days before the bot started working are not counted); no "runs out on" date is given until that window
is at least one day long.
"""

import calendar
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from iler import settings_store
from iler.config import brand_company, brand_name, gateway_mode, get_settings, topup_url
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
ALERT_LEVELS = (80, 95, 100)  # spent share: 20 % left, 5 % left, exhausted
_CACHE_S = 30.0


class CoinsExhausted(Exception):
    """The coins are used up: the local monthly limit, or the gateway balance (HTTP 402)."""


class AccessSuspended(CoinsExhausted):
    """The LLM gateway revoked or suspended the install's key (HTTP 401 key_revoked)."""


class UsageAlerts(BaseModel):
    month: str = ""  # "2026-11"
    limit: int | None = None  # the limit the levels were sent for; a new limit re-arms the alerts
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


def _match(model: str, table: dict[str, tuple[float, float]]) -> str | None:
    return max((p for p in table if model.startswith(p)), key=len, default=None)


def price_for(model: str, table: dict[str, tuple[float, float]] | None = None) -> tuple[float, float]:
    table = table if table is not None else prices()
    match = _match(model, table)
    return table[match] if match else FALLBACK_PRICE


def is_priced(model: str, table: dict[str, tuple[float, float]] | None = None) -> bool:
    return _match(model, table if table is not None else prices()) is not None


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
    avg_per_day: float  # pace since max(month start, first request of the month); at least a 1-day window
    projected: float  # spent + pace * days left in the month
    runs_out_on: date | None  # day the limit is reached at the current pace (None: lasts the month / too early)
    days_in_month: int
    alerts: list[int] = field(default_factory=list)
    unpriced_models: list[str] = field(default_factory=list)  # used this month, billed at FALLBACK_PRICE

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
        " sum(input_tokens)::bigint AS tin, sum(output_tokens)::bigint AS tout, min(created_at) AS first_at"
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
    # The pace counts only the time the bot has been spending this month: from the later of the month start
    # and the first request (a bot installed on the 20th must not look cheap because of 19 empty days).
    first_at = min((r["first_at"] for r in rows), default=None)
    pace_start = max(start.astimezone(UTC), first_at) if first_at else start.astimezone(UTC)
    elapsed = max((now - pace_start).total_seconds() / 86400, 0.0)
    avg = spent / max(elapsed, 1.0)  # within the first day: what was spent so far counts as one day's pace
    month_end = (start + timedelta(days=dim)).replace(tzinfo=None).replace(tzinfo=tz)  # DST-safe next month
    days_left = max((month_end.astimezone(UTC) - now).total_seconds() / 86400, 0.0)
    limit = max(get_settings().monthly_coins, 0)
    runs_out = None
    if limit and spent >= limit:
        runs_out = local_today
    elif limit and avg > 0 and elapsed >= 1.0:  # less than a day of data is too little for a date
        when = now + timedelta(days=(limit - spent) / avg)
        if when < month_end.astimezone(UTC):
            runs_out = when.astimezone(tz).date()
    alerts = await settings_store.get("usage_alerts", UsageAlerts)
    return MonthUsage(
        limit=limit,
        spent=round(spent, 2),
        days=days,
        by_model=sorted(((m, round(c, 2)) for m, c in per_model.items()), key=lambda x: -x[1]),
        avg_per_day=round(avg, 2),
        projected=round(spent + avg * days_left, 2),
        runs_out_on=runs_out,
        days_in_month=dim,
        alerts=alerts.sent if alerts.month == f"{start:%Y-%m}" and alerts.limit in (None, limit) else [],
        unpriced_models=sorted(m for m in per_model if not is_priced(m, table)),
    )


_cache: tuple[float, bool] | None = None


def invalidate() -> None:
    global _cache
    _cache = None


async def limit_reached() -> bool:
    """Cheap check before each bot turn (cached for a few seconds)."""
    global _cache
    if get_settings().monthly_coins <= 0 or gateway_mode():  # in gateway mode the gateway decides
        return False
    if _cache and time.monotonic() - _cache[0] < _CACHE_S:
        return _cache[1]
    reached = (await month_usage()).exhausted
    _cache = (time.monotonic(), reached)
    return reached


@dataclass
class _AlertSource:
    """What the coin alerts are computed from: the local monthly limit or the gateway balance."""

    state_key: str  # settings_store key of the UsageAlerts state
    period: str  # month ("2026-11") or the gateway period end
    limit: int
    spent: float
    key_prefix: str  # idempotency key prefix of the WhatsApp alert
    runs_out_on: date | None = None
    gateway: bool = False


async def _local_source() -> _AlertSource:
    usage = await month_usage()
    return _AlertSource(state_key="usage_alerts", period=f"{month_start(datetime.now(UTC), _tz()):%Y-%m}",
                        limit=usage.limit, spent=usage.spent, key_prefix="coins", runs_out_on=usage.runs_out_on)


async def _gateway_source() -> _AlertSource | None:
    from iler.llm import gateway

    view = await gateway.balance()
    b = view.balance
    if b is None or b.monthly_limit <= 0:
        return None
    limit = max(int(round(b.monthly_limit)), 1)
    spent = max(b.monthly_limit - b.coins_left, 0.0)
    if view.blocked == gateway.INSUFFICIENT_COINS:
        spent = max(spent, float(limit))  # the gateway said 402: the coins are gone whatever the cache says
    period = b.period_end or f"{month_start(datetime.now(UTC), _tz()):%Y-%m}"
    return _AlertSource(state_key="usage_alerts_gateway", period=period, limit=limit, spent=spent,
                        key_prefix="coins-gw", gateway=True)


def _alert_text(src: _AlertSource, level: int) -> str:
    if src.gateway:
        if level >= 100:
            topup = topup_url()
            how = f"Пополнить: {topup}" if topup else f"Чтобы пополнить баланс, обратитесь в {brand_company()}."
            return (f"{brand_name()} Bot: коины закончились. Бот больше не отвечает сам: клиент получает "
                    f"сообщение «сейчас подключится менеджер», диалоги ждут оператора в админке. {how}")
        pct = math.floor(src.spent / src.limit * 100 + 1e-9)
        left = max(src.limit - src.spent, 0.0)
        return (f"{brand_name()} Bot: израсходовано {pct}% коинов ({src.spent:.0f} из {src.limit}), "
                f"осталось {left:.0f}.")
    if level >= 100:
        return (f"{brand_name()} Bot: лимит коинов на месяц исчерпан ({src.spent:.0f} из {src.limit}). "
                "Бот больше не отвечает сам: новые сообщения ждут оператора в админке. "
                f"Чтобы увеличить лимит, обратитесь в {brand_company()}.")
    pct = math.floor(src.spent / src.limit * 100 + 1e-9)  # floor: never claim more than was used
    out = f" При текущем темпе лимит закончится {src.runs_out_on:%d.%m}." if src.runs_out_on else ""
    return f"{brand_name()} Bot: израсходовано {pct}% коинов на месяц ({src.spent:.0f} из {src.limit}).{out}"


async def after_spend() -> None:
    """Called after an LLM turn: refresh the cache and send the 80% / 95% / 100% alerts once per period
    and limit (the local month and limit, or the gateway period and monthly limit in gateway mode).

    A level is marked as sent only when WhatsApp took the alert for at least one admin target; otherwise
    it is retried on the next call (the idempotency key keeps already-delivered targets from repeats).
    With no admin targets at all the level is marked anyway, so it is not retried on every turn."""
    invalidate()
    if not gateway_mode() and get_settings().monthly_coins <= 0:
        return
    try:
        src = await (_gateway_source() if gateway_mode() else _local_source())
        if src is None:
            return
        state = await settings_store.get(src.state_key, UsageAlerts)
        if state.month != src.period:
            state = UsageAlerts(month=src.period, limit=src.limit)
        elif state.limit is None:  # saved before the limit was tracked: adopt it, keep what was sent
            state.limit = src.limit
        elif state.limit != src.limit:  # the limit changed: re-evaluate the levels for the new one
            state = UsageAlerts(month=src.period, limit=src.limit)
        # same comparison as MonthUsage.exhausted (no rounding of the percentage)
        due = [lvl for lvl in ALERT_LEVELS if src.spent >= src.limit * lvl / 100 and lvl not in state.sent]
        if not due:
            await settings_store.put(src.state_key, state)
            return
        level = max(due)
        from iler.engine import sender
        from iler.settings_models import NotifySettings

        statuses = await sender.notify_admins(_alert_text(src, level),
                                              key=f"{src.key_prefix}:{src.period}:{src.limit}:{level}")
        if not any(st != "failed" for st in statuses):
            targets = sender.admin_targets(await settings_store.get("notify", NotifySettings))
            if targets:
                log.warning("coin alert %s%% was not delivered to admins; will retry", level)
                await settings_store.put(src.state_key, state)
                return
            log.warning("coin alert %s%% not sent: no admin phones or group configured", level)
        state.sent = sorted(set(state.sent) | set(due))
        await settings_store.put(src.state_key, state)
    except Exception:  # noqa: BLE001 - accounting must never break a reply
        log.exception("coin usage check failed")
