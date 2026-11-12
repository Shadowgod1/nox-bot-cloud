"""Overview screen data (spec section 6.3). "Today" uses the club's time zone (ILER_TZ) day boundaries."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from iler import settings_store, usage
from iler.auth.deps import User, current_user
from iler.backup import status as backup_status
from iler.config import get_settings
from iler.db.pool import get_pool
from iler.settings_models import BotSettings
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
                           AND b.text <> $4)) AS answered_convs
        """,
        start, end, since, HANDOVER_REPLY,
    )
    wa = await monitor.get_status()
    bot = await settings_store.get("bot", BotSettings)
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


async def _coins(now: datetime) -> dict:
    u = await usage.month_usage(now)
    return {
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
    }
