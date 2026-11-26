"""Overview screen data (spec section 6.3). "Today" uses the club's time zone (ILER_TZ) day boundaries."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from iler import settings_store, usage
from iler.auth.deps import User, current_user
from iler.backup import status as backup_status
from iler.config import gateway_mode, get_settings, topup_url
from iler.db.pool import get_pool
from iler.errors import ApiError
from iler.settings_models import NO_COINS_MESSAGE_DEFAULT, BotSettings
from iler.whatsapp import monitor

# the fixed "passing you to an administrator" reply (kept in sync with iler.engine.turn.HANDOVER_REPLY;
# not imported so the dashboard does not pull in the engine)
HANDOVER_REPLY = "Передаю ваш вопрос администратору, он скоро ответит."

router = APIRouter(tags=["dashboard"])


class WhatsAppBrief(BaseModel):
    status: str
    phone: str | None


class TodayStats(BaseModel):
    conversations: int  # distinct conversations with at least one message today
    client_messages: int
    bot_replies: int
    bookings_pending: int  # all requests currently awaiting a decision (not only today's)
    bookings_total: int  # requests created today


class Errors24h(BaseModel):
    llm: int  # failed LLM attempts (ai_requests.error) in the last 24 h
    send: int  # outbound messages that ended failed/uncertain in the last 24 h


class BackupBrief(BaseModel):
    status: str | None  # ok | error | None (no backup has run yet)
    at: str | None  # ISO-8601 time of the last attempt
    message: str | None


class Health(BaseModel):
    last_bot_reply_at: str | None  # ISO-8601 time of the latest bot message
    last_client_message_at: str | None
    avg_reply_ms_today: int | None  # mean LLM latency of today's successful requests
    # conversations that got status needs_operator today for any reason (handover tool, LLM failure, coin
    # limit): conversations.needs_operator_at, plus successful handover tool calls for older rows
    handovers_today: int
    # share (0..1) of today's client conversations the bot answered itself; the "passing you to an
    # administrator" reply (coin limit / LLM failure handover) does not count as an answer
    answered_share_today: float | None


class CoinDay(BaseModel):
    date: str  # YYYY-MM-DD (club time zone)
    coins: float


class CoinModel(BaseModel):
    model: str
    coins: float


class Coins(BaseModel):
    limit: int  # 0 = no limit
    spent: float
    remaining: float | None
    percent: float | None
    exhausted: bool
    avg_per_day: float
    projected_month: float
    runs_out_on: str | None  # YYYY-MM-DD when the limit runs out at the current pace; None = lasts
    days_in_month: int
    days: list[CoinDay]
    by_model: list[CoinModel]
    unpriced_models: list[str]  # used this month with no price entry: billed at the highest tariff
    # "local": the monthly limit of this install (ILER_MONTHLY_COINS); "gateway": limit / remaining / spent
    # come from the LLM gateway balance (days / by_model stay the local per-model stats)
    source: Literal["local", "gateway"] = "local"
    period_end: str | None = None  # gateway: end of the paid period
    stale: bool = False  # gateway: the balance could not be read just now; the last known value is shown
    balance_at: str | None = None  # gateway: ISO-8601 time the shown balance was read
    # gateway: "insufficient_coins" / "key_revoked" while the bot hands every conversation to the operator
    blocked: str | None = None
    topup_url: str | None = None  # where the owner tops up coins (ILER_TOPUP_URL), if set


class Dashboard(BaseModel):
    whatsapp: WhatsAppBrief
    bot_enabled: bool
    today: TodayStats
    errors_24h: Errors24h
    backup: BackupBrief
    health: Health
    coins: Coins


def _now() -> datetime:
    return datetime.now(UTC)


def today_bounds(now: datetime, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=1)).replace(tzinfo=None).replace(tzinfo=tz)  # DST-safe next midnight
    return start.astimezone(UTC), end.astimezone(UTC)


@router.get("/api/dashboard", response_model=Dashboard)
async def get_dashboard(_user: User = Depends(current_user)):
    now = _now()
    start, end = today_bounds(now, get_settings().tz)
    since = now - timedelta(hours=24)
    pool = get_pool()
    bot = await settings_store.get("bot", BotSettings)
    row = await pool.fetchrow(
        """
        SELECT
          (SELECT count(DISTINCT conversation_id) FROM messages
             WHERE created_at >= $1 AND created_at < $2) AS conversations,
          (SELECT count(*) FROM messages
             WHERE author = 'client' AND created_at >= $1 AND created_at < $2) AS client_messages,
          (SELECT count(*) FROM messages
             WHERE author = 'bot' AND created_at >= $1 AND created_at < $2) AS bot_replies,
          (SELECT count(*) FROM booking_requests WHERE status = 'pending') AS bookings_pending,
          (SELECT count(*) FROM booking_requests
             WHERE created_at >= $1 AND created_at < $2) AS bookings_total,
          (SELECT count(*) FROM ai_requests WHERE error IS NOT NULL AND created_at >= $3) AS llm_errors,
          (SELECT count(*) FROM outbound_intents
             WHERE status IN ('failed', 'uncertain') AND updated_at >= $3) AS send_errors,
          (SELECT max(created_at) FROM messages WHERE author = 'bot') AS last_bot_at,
          (SELECT max(created_at) FROM messages WHERE author = 'client') AS last_client_at,
          (SELECT avg(latency_ms) FROM ai_requests
             WHERE error IS NULL AND created_at >= $1 AND created_at < $2) AS avg_latency,
          (SELECT count(*) FROM (
             SELECT id FROM conversations WHERE needs_operator_at >= $1 AND needs_operator_at < $2
             UNION
             SELECT ai_requests.conversation_id FROM tool_calls
               JOIN ai_requests ON ai_requests.id = tool_calls.ai_request_id
               WHERE tool_calls.name = 'handover_to_operator' AND tool_calls.error IS NULL
                 AND ai_requests.conversation_id IS NOT NULL
                 AND tool_calls.created_at >= $1 AND tool_calls.created_at < $2) h) AS handovers,
          (SELECT count(DISTINCT conversation_id) FROM messages
             WHERE author = 'client' AND created_at >= $1 AND created_at < $2) AS client_convs,
          (SELECT count(DISTINCT m.conversation_id) FROM messages m
             WHERE m.author = 'client' AND m.created_at >= $1 AND m.created_at < $2
               AND EXISTS (SELECT 1 FROM messages b WHERE b.conversation_id = m.conversation_id
                           AND b.author = 'bot' AND b.created_at >= $1 AND b.created_at < $2
                           AND b.text <> ALL($4::text[]))) AS answered_convs
        """,
        start, end, since, _handover_texts(bot),
    )
    wa = await monitor.get_status()
    return {
        "whatsapp": {"status": wa["status"], "phone": wa.get("phone")},
        "bot_enabled": bot.enabled,
        "today": {
            "conversations": row["conversations"],
            "client_messages": row["client_messages"],
            "bot_replies": row["bot_replies"],
            "bookings_pending": row["bookings_pending"],
            "bookings_total": row["bookings_total"],
        },
        "errors_24h": {"llm": row["llm_errors"], "send": row["send_errors"]},
        "backup": await backup_status.get(),
        "health": {
            "last_bot_reply_at": _iso(row["last_bot_at"]),
            "last_client_message_at": _iso(row["last_client_at"]),
            "avg_reply_ms_today": int(row["avg_latency"]) if row["avg_latency"] is not None else None,
            "handovers_today": row["handovers"],
            "answered_share_today": (round(row["answered_convs"] / row["client_convs"], 3)
                                     if row["client_convs"] else None),
        },
        "coins": await _coins(now),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _handover_texts(bot: BotSettings) -> list[str]:
    """Bot replies that are not answers: the "passing you to an administrator" reply and the holding
    reply sent when the coins are used up (engine.turn.no_coins_message)."""
    return [HANDOVER_REPLY, (bot.no_coins_message or "").strip() or NO_COINS_MESSAGE_DEFAULT]


async def _coins(now: datetime) -> dict:
    u = await usage.month_usage(now)
    coins = {
        "limit": u.limit,
        "spent": u.spent,
        "remaining": round(u.remaining, 2) if u.remaining is not None else None,
        "percent": u.percent,
        "exhausted": u.exhausted,
        "avg_per_day": u.avg_per_day,
        "projected_month": u.projected,
        "runs_out_on": u.runs_out_on.isoformat() if u.runs_out_on else None,
        "days_in_month": u.days_in_month,
        "days": [{"date": d.isoformat(), "coins": c} for d, c in u.days],
        "by_model": [{"model": m, "coins": c} for m, c in u.by_model],
        "unpriced_models": u.unpriced_models,
        "topup_url": topup_url(),
    }
    if gateway_mode():
        coins.update(await _gateway_coins(now, u))
    return coins


async def _gateway_coins(now: datetime, u: usage.MonthUsage) -> dict:
    """Gateway mode: the gateway balance replaces the local limit (cached, never raises)."""
    from iler.llm import gateway

    view = await gateway.balance()
    out: dict = {"source": "gateway", "stale": view.stale, "blocked": view.blocked,
                 "balance_at": _iso(view.fetched_at), "runs_out_on": None}
    b = view.balance
    if b is None:  # never read: no limit to show, only the local spend
        out.update({"limit": 0, "remaining": None, "percent": None, "exhausted": view.blocked is not None,
                    "period_end": None})
        return out
    limit = max(b.monthly_limit, 0.0)
    left = max(b.coins_left, 0.0)
    if view.blocked == gateway.INSUFFICIENT_COINS:
        left = 0.0  # the gateway said 402 after this balance was read
    spent = max(limit - left, 0.0)
    out.update({
        "limit": int(round(limit)),
        "spent": round(spent, 2),
        "remaining": round(left, 2),
        "percent": round(spent / limit * 100, 1) if limit else None,
        "exhausted": left <= 0 or view.blocked is not None,
        "period_end": b.period_end,
    })
    # forecast from the local pace of this month (the gateway has no history API)
    end = _parse_time(b.period_end)
    if end is not None and end > now:
        days_left = (end - now).total_seconds() / 86400
        out["projected_month"] = round(spent + u.avg_per_day * days_left, 2)
        if left > 0 and u.avg_per_day > 0:
            when = now + timedelta(days=left / u.avg_per_day)
            if when < end:
                out["runs_out_on"] = when.astimezone(ZoneInfo(get_settings().tz)).date().isoformat()
    return out


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --- payback stats ("что бот сделал за 7 / 30 дней") ----------------------------------------------

DEFAULT_OFF_HOURS = ("21:00", "09:00")  # bot hours "always open": 21:00–09:00 counts as after hours


class RoiStats(BaseModel):
    days: Literal[7, 30]
    since: str  # ISO-8601 start of the period (club time zone midnight)
    bot_conversations: int  # conversations the bot answered itself (handover / no-coins replies excluded)
    bookings: int  # booking requests the bot collected
    after_hours_conversations: int  # conversations the bot answered outside working hours
    handovers: int  # conversations passed to the operator (any reason)
    coins: float  # LLM spend in the period (test chat and price photos included)
    hours_source: Literal["settings", "default"]  # working hours from the bot settings, or 09:00–21:00
    has_activity: bool  # any client message ever (False: a new bot, the admin shows the empty state)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_after_hours(local: datetime, bot: BotSettings) -> bool:
    """Outside working hours: the bot's weekly hours when set, else 21:00–09:00 every day."""
    t = local.hour * 60 + local.minute
    if bot.hours.always_open:
        start, end = (_minutes(x) for x in DEFAULT_OFF_HOURS)
        return t >= start or t < end
    day = getattr(bot.hours, ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[local.weekday()])
    if day.closed:
        return True
    open_, close = _minutes(day.open), _minutes(day.close)
    if close <= open_:  # past midnight, e.g. 10:00–02:00
        return not (t >= open_ or t < close)
    return not (open_ <= t < close)


@router.get("/api/dashboard/roi", response_model=RoiStats)
async def get_dashboard_roi(days: int = 7, _user: User = Depends(current_user)):
    if days not in (7, 30):
        raise ApiError(422, "validation_error", "days: 7 or 30")
    now = _now()
    tz_name = get_settings().tz
    tz = ZoneInfo(tz_name)
    today_start, _ = today_bounds(now, tz_name)
    since = (today_start.astimezone(tz) - timedelta(days=days - 1)).replace(tzinfo=None).replace(tzinfo=tz)
    since_utc = since.astimezone(UTC)
    pool = get_pool()
    bot = await settings_store.get("bot", BotSettings)
    # fixed replies are not the bot doing the work: handover, no coins, "write in text" / "photo to the master"
    skip = [*_handover_texts(bot), bot.voice_message.strip(), bot.photo_message.strip()]
    row = await pool.fetchrow(
        """
        SELECT
          (SELECT count(DISTINCT conversation_id) FROM messages
             WHERE author = 'bot' AND created_at >= $1 AND text <> ALL($2::text[])) AS bot_convs,
          (SELECT count(*) FROM booking_requests WHERE created_at >= $1) AS bookings,
          (SELECT count(*) FROM (
             SELECT id FROM conversations WHERE needs_operator_at >= $1
             UNION
             SELECT ai_requests.conversation_id FROM tool_calls
               JOIN ai_requests ON ai_requests.id = tool_calls.ai_request_id
               WHERE tool_calls.name = 'handover_to_operator' AND tool_calls.error IS NULL
                 AND ai_requests.conversation_id IS NOT NULL AND tool_calls.created_at >= $1) h) AS handovers,
          EXISTS (SELECT 1 FROM messages WHERE author = 'client') AS has_activity
        """,
        since_utc, skip,
    )
    replies = await pool.fetch(
        "SELECT conversation_id, created_at FROM messages"
        " WHERE author = 'bot' AND created_at >= $1 AND text <> ALL($2::text[])",
        since_utc, skip,
    )
    # working hours are set in the bot's own time zone (reply rules), the period in the install's
    try:
        hours_tz = ZoneInfo((bot.timezone or "").strip() or tz_name)
    except Exception:  # noqa: BLE001 - validated on save; never break the dashboard
        hours_tz = tz
    after_hours = {r["conversation_id"] for r in replies if is_after_hours(r["created_at"].astimezone(hours_tz), bot)}
    spend = await pool.fetch(
        "SELECT model, sum(input_tokens)::bigint AS tin, sum(output_tokens)::bigint AS tout"
        " FROM ai_requests WHERE created_at >= $1 GROUP BY model",
        since_utc,
    )
    table = usage.prices()
    coins = sum(usage.coins_for(r["model"], r["tin"], r["tout"], table) for r in spend)
    return {
        "days": days,
        "since": since.isoformat(),
        "bot_conversations": row["bot_convs"],
        "bookings": row["bookings"],
        "after_hours_conversations": len(after_hours),
        "handovers": row["handovers"],
        "coins": round(coins, 2),
        "hours_source": "default" if bot.hours.always_open else "settings",
        "has_activity": row["has_activity"],
    }
