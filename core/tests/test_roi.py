"""Payback stats on the dashboard: 7 / 30 days, after-hours by working hours, empty state of a new bot."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from iler import settings_store
from iler.api import dashboard
from iler.settings_models import BotSettings, DayHours, WeeklyHours

OWNER = {"email": "owner@salon.kz", "password": "owner-pass-123", "name": "Owner"}
TZ = ZoneInfo("Asia/Almaty")


@pytest.fixture
async def owner(client):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    return client


async def _conv(pool, phone: str) -> int:
    return await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone, name) VALUES ($1, $2, '') RETURNING id",
        f"{phone}@s.whatsapp.net", phone,
    )


async def _msg(pool, conv: int, author: str, text: str, at: datetime) -> None:
    await pool.execute(
        "INSERT INTO messages(conversation_id, direction, author, text, created_at) VALUES ($1, $2, $3, $4, $5)",
        conv, "in" if author == "client" else "out", author, text, at,
    )


async def test_new_bot_is_empty(owner):
    r = await owner.get("/api/dashboard/roi?days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_activity"] is False and body["days"] == 7
    assert body["bot_conversations"] == body["bookings"] == body["after_hours_conversations"] == body["handovers"] == 0
    assert body["coins"] == 0 and body["hours_source"] == "default"
    assert (await owner.get("/api/dashboard/roi?days=5")).status_code == 422


async def test_counts_in_period(owner, pool):
    now = datetime.now(UTC)
    local_today = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    night = (local_today - timedelta(days=1)).replace(hour=23, minute=30)  # after hours (21:00–09:00)
    noon = (local_today - timedelta(days=1)).replace(hour=13)
    a, b, c, old = [await _conv(pool, p) for p in ("77010000001", "77010000002", "77010000003", "77010000004")]
    await _msg(pool, a, "client", "Сколько стоит маникюр?", night)
    await _msg(pool, a, "bot", "От 6000 ₸", night + timedelta(minutes=1))
    await _msg(pool, b, "client", "Можно в четверг?", noon)
    await _msg(pool, b, "bot", "Да, Айгерим работает с 10 до 19", noon + timedelta(minutes=1))
    await _msg(pool, c, "client", "Жалоба", noon)
    await _msg(pool, c, "bot", dashboard.HANDOVER_REPLY, noon + timedelta(minutes=1))  # not an answer
    await pool.execute("UPDATE conversations SET needs_operator_at = $2 WHERE id = $1", c, noon)
    await _msg(pool, old, "bot", "давний ответ", now - timedelta(days=20))
    await pool.execute("INSERT INTO booking_requests(conversation_id, payload, status) VALUES ($1, '{}', 'pending')", b)
    await pool.execute("INSERT INTO ai_requests(provider, model, input_tokens, output_tokens, latency_ms, created_at)"
                       " VALUES ('anthropic', 'claude-haiku-5-5', 1000000, 0, 10, $1)", noon)

    week = (await owner.get("/api/dashboard/roi?days=7")).json()
    assert week["has_activity"] is True
    assert week["bot_conversations"] == 2 and week["bookings"] == 1 and week["handovers"] == 1
    assert week["after_hours_conversations"] == 1
    assert week["coins"] == 100.0  # 1M haiku input tokens = $0.10
    month = (await owner.get("/api/dashboard/roi?days=30")).json()
    assert month["bot_conversations"] == 3


def test_after_hours_uses_bot_hours_when_set():
    always = BotSettings(prompt="x")
    assert dashboard.is_after_hours(datetime(2026, 11, 26, 22, 0, tzinfo=TZ), always)
    assert dashboard.is_after_hours(datetime(2026, 11, 26, 8, 59, tzinfo=TZ), always)
    assert not dashboard.is_after_hours(datetime(2026, 11, 26, 9, 0, tzinfo=TZ), always)
    day = DayHours(open="10:00", close="19:00")
    hours = WeeklyHours(always_open=False, mon=day, tue=day, wed=day, thu=day, fri=day, sat=day,
                        sun=DayHours(closed=True))
    salon = BotSettings(prompt="x", hours=hours)
    thursday = datetime(2026, 11, 26, 19, 30, tzinfo=TZ)
    assert thursday.weekday() == 3
    assert dashboard.is_after_hours(thursday, salon)
    assert not dashboard.is_after_hours(thursday.replace(hour=12), salon)
    assert dashboard.is_after_hours(datetime(2026, 11, 29, 12, 0, tzinfo=TZ), salon)  # Sunday, closed


async def test_settings_hours_source(owner):
    hours = WeeklyHours(always_open=False)
    await settings_store.put("bot", BotSettings(prompt="x", hours=hours))
    assert (await owner.get("/api/dashboard/roi?days=30")).json()["hours_source"] == "settings"
