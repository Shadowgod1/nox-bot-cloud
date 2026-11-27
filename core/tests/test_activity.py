"""Overview charts: per-day activity, client hours, median reply time, the trend against the previous period."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

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


async def _msg(pool, conv: int, author: str, at: datetime) -> None:
    await pool.execute(
        "INSERT INTO messages(conversation_id, direction, author, text, created_at) VALUES ($1, $2, $3, 'x', $4)",
        conv, "in" if author == "client" else "out", author, at,
    )


async def test_empty_period(owner):
    r = await owner.get("/api/dashboard/activity?days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["series"]) == 7 and body["hours"] == [0] * 24
    assert body["series"][-1]["date"] == datetime.now(UTC).astimezone(TZ).date().isoformat()
    assert body["totals"]["client_messages"] == 0 and body["median_reply_seconds"] is None
    assert (await owner.get("/api/dashboard/activity?days=5")).status_code == 422


async def test_series_hours_reply_time_and_trend(owner, pool):
    today = datetime.now(UTC).astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    y10 = (today - timedelta(days=1)).replace(hour=10)
    a, b, old = [await _conv(pool, p) for p in ("77010000001", "77010000002", "77010000003")]
    await _msg(pool, a, "client", y10)
    await _msg(pool, a, "bot", y10 + timedelta(seconds=20))
    await _msg(pool, a, "client", y10 + timedelta(minutes=5))
    await _msg(pool, a, "bot", y10 + timedelta(minutes=5, seconds=40))
    await _msg(pool, b, "client", today.replace(hour=22))
    await pool.execute("INSERT INTO booking_requests(conversation_id, payload, status) VALUES ($1, '{}', 'pending')", a)
    await pool.execute("UPDATE conversations SET needs_operator_at = $2 WHERE id = $1", b, today.replace(hour=22))
    await _msg(pool, old, "client", today - timedelta(days=10))  # previous 7-day period

    body = (await owner.get("/api/dashboard/activity?days=7")).json()
    by_date = {d["date"]: d for d in body["series"]}
    y = by_date[y10.date().isoformat()]
    assert (y["conversations"], y["client_messages"], y["bot_replies"]) == (1, 2, 2)
    t = by_date[today.date().isoformat()]
    assert (t["conversations"], t["client_messages"], t["handovers"]) == (1, 1, 1)
    assert sum(d["bookings"] for d in body["series"]) == 1
    assert body["hours"][10] == 2 and body["hours"][22] == 1
    assert body["median_reply_seconds"] == 30  # 20 s and 40 s
    assert body["totals"] == {"conversations": 2, "client_messages": 3, "bot_replies": 2, "bookings": 1,
                              "handovers": 1}
    assert body["previous"]["client_messages"] == 1
