"""Overview screen data (spec section 6.3). "Today" uses the club's time zone (ILER_TZ) day boundaries."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from iler import settings_store
from iler.auth.deps import User, current_user
from iler.config import get_settings
from iler.db.pool import get_pool
from iler.settings_models import BotSettings
from iler.whatsapp import monitor

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


class Dashboard(BaseModel):
    whatsapp: WhatsAppBrief
    bot_enabled: bool
    today: TodayStats
    errors_24h: Errors24h


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
             WHERE status IN ('failed', 'uncertain') AND updated_at >= $3) AS send_errors
        """,
        start, end, since,
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
    }
