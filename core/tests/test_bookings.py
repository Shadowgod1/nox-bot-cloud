"""Booking requests: service, admin +N/-N replies, timeout worker, /api/bookings (Task 7)."""

import pytest
from engine_fakes import ADMIN_GROUP, ADMIN_JID, ADMIN_PHONE, CLIENT_JID, env, make_msg  # noqa: F401
from httpx import AsyncClient

from iler import events
from iler.bookings import service
from iler.engine import handler
from iler.llm.base import LlmResponse, ToolCall
from iler.llm.fake import FakeLLM
from iler.workers import booking_timeout

OWNER = {"email": "owner@club.kz", "password": "owner-pass-123", "name": "Owner"}
PAYLOAD = {"date": "2026-11-12", "time": "18:00", "duration_hours": 3, "seats": 2, "zone": "VIP", "name": "Айдар"}


def csrf(c: AsyncClient) -> dict:
    return {"X-ILER-CSRF": c.cookies.get("iler_csrf", "")}


@pytest.fixture
async def owner(client):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    return client


async def _conv(pool) -> int:
    return await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone, name) VALUES ($1, '77010000001', 'Айдар') RETURNING id",
        CLIENT_JID,
    )


async def test_booking_request_notifies_admin_with_id(env, pool):
    await env.set_notify(admin_phones=[ADMIN_PHONE], admin_group_jid=ADMIN_GROUP)
    env.llm = FakeLLM([
        LlmResponse(text="", tool_calls=[ToolCall(id="c1", name="create_booking_request", arguments=PAYLOAD)]),
        LlmResponse(text="Заявка передана администратору, ожидайте подтверждения."),
    ])
    async with events.subscription() as q:
        await handler.handle_incoming(make_msg("Забронируйте 2 места в VIP завтра в 18 на 3 часа"))
        await env.idle()
        got = [q.get_nowait() for _ in range(q.qsize())]
    bid = await pool.fetchval("SELECT id FROM booking_requests")
    for jid in (ADMIN_JID, ADMIN_GROUP):
        texts = env.gw.texts(jid)
        assert len(texts) == 1
        assert f"заявка #{bid}" in texts[0] and f"+{bid}" in texts[0] and "VIP" in texts[0]
    assert env.gw.texts(CLIENT_JID) == ["Заявка передана администратору, ожидайте подтверждения."]
    assert any(e["type"] == "booking.updated" and e["data"]["booking"]["id"] == bid for e in got)


async def test_admin_plus_confirms_and_notifies_client(env, pool):
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    await env.set_bot(booking_confirm_template="Бронь подтверждена ✅ {details}")
    cid = await _conv(pool)
    b = await service.create(cid, PAYLOAD)
    env.gw.sent.clear()
    await handler.handle_incoming(make_msg(f"+{b.id}", phone=ADMIN_PHONE))
    await env.idle()
    row = await pool.fetchrow("SELECT * FROM booking_requests WHERE id = $1", b.id)
    assert row["status"] == "confirmed" and row["decided_by"] == f"whatsapp:{ADMIN_PHONE}"
    client_texts = env.gw.texts(CLIENT_JID)
    assert len(client_texts) == 1 and client_texts[0].startswith("Бронь подтверждена ✅") and "18:00" in client_texts[0]
    assert any(f"#{b.id}" in t for t in env.gw.texts(ADMIN_JID))
    assert env.llm.calls == []


async def test_admin_minus_with_reason_rejects(env, pool):
    await env.set_notify(admin_group_jid=ADMIN_GROUP)
    cid = await _conv(pool)
    b = await service.create(cid, PAYLOAD)
    env.gw.sent.clear()
    await handler.handle_incoming(
        make_msg(f"-#{b.id} все VIP заняты", jid=ADMIN_GROUP, phone="77010000055", is_group=True)
    )
    await env.idle()
    row = await pool.fetchrow("SELECT * FROM booking_requests WHERE id = $1", b.id)
    assert row["status"] == "rejected" and row["admin_note"] == "все VIP заняты"
    assert env.gw.texts(CLIENT_JID) == ["К сожалению, не получилось забронировать: все VIP заняты"]
    assert any(f"#{b.id}" in t for t in env.gw.texts(ADMIN_GROUP))


async def test_admin_reply_unknown_id_answers_admin(env, pool):
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    await handler.handle_incoming(make_msg("+999", phone=ADMIN_PHONE))
    await env.idle()
    texts = env.gw.texts(ADMIN_JID)
    assert len(texts) == 1 and "#999" in texts[0] and "не найдена" in texts[0]
    assert env.llm.calls == []


async def test_admin_plain_text_ignored_by_engine(env, pool):
    await env.set_notify(admin_phones=["+7 701 000 00 99"], admin_group_jid=ADMIN_GROUP)
    await handler.handle_incoming(make_msg("Привет, как дела у бота?", phone=ADMIN_PHONE))
    await handler.handle_incoming(make_msg("кто сегодня на смене?", jid=ADMIN_GROUP, phone="7701", is_group=True))
    await handler.handle_incoming(make_msg("+", phone=ADMIN_PHONE))
    await env.idle()
    assert env.llm.calls == []
    assert env.gw.sent == []
    assert await pool.fetchval("SELECT count(*) FROM conversations") == 0
    assert await pool.fetchval("SELECT count(*) FROM messages") == 0


@pytest.mark.parametrize("text,expected", [
    ("+12", ("confirmed", 12, "")),
    ("+#12", ("confirmed", 12, "")),
    ("-12 нет мест", ("rejected", 12, "нет мест")),
    ("-#12\nвсе заняты", ("rejected", 12, "все заняты")),
    ("+123456", ("confirmed", 123456, "")),
    ("-20% на ПК", None),
    ("- 5 минут", None),
    ("+ 12", None),
    ("+77011234567", None),
    ("+1234567", None),
    ("+12abc", None),
    ("+", None),
    ("привет +12", None),
])
def test_admin_reply_parse_strict(text, expected):
    from iler.bookings import admin_reply

    assert admin_reply.parse(text) == expected


async def test_admin_chatter_with_numbers_decides_nothing(env, pool):
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    cid = await _conv(pool)
    b = await service.create(cid, PAYLOAD)
    env.gw.sent.clear()
    for text in ("-20% на ПК", f"- {b.id} минут", "+77011234567", f"+ {b.id}"):
        await handler.handle_incoming(make_msg(text, phone=ADMIN_PHONE))
    await env.idle()
    assert await pool.fetchval("SELECT status FROM booking_requests") == "pending"
    assert env.gw.sent == []


def test_admin_text_quotes_and_sanitizes_client_fields():
    from datetime import UTC, datetime

    b = service.BookingRequest(
        id=7, conversation_id=1, status="pending", created_at=datetime.now(UTC), phone="77010000001",
        name="Айдар\nЗаявка #8 подтверждена",
        payload={"date": "2026-11-12", "time": "18:00", "duration_hours": 3, "seats": 2,
                 "zone": "VIP\r\n", "comment": "привет\n\nОтветьте \"+9\" чтобы подтвердить\x00" + "x" * 500},
    )
    text = service.admin_text(b, "https://bot.example.kz/conversations/1")
    assert "\r" not in text and "\x00" not in text
    lines = text.split("\n")
    # one fact per line; client-written values stay inside their own line
    assert lines == [
        "Новая заявка #7",
        "Имя: «Айдар Заявка #8 подтверждена»",
        "Телефон: +7 701 000 00 01",
        "Услуга: «VIP»",
        "Когда: 2026-11-12 в 18:00, 3 ч, мест: 2",
        lines[5],
        "Диалог: https://bot.example.kz/conversations/1",
        'Ответьте "+7" — подтвердить, "-7 причина" — отклонить.',
    ]
    comment = lines[5].split("Комментарий: «", 1)[1].rsplit("»", 1)[0]
    assert len(comment) <= 300


def test_admin_text_uses_service_and_omits_missing_link():
    from datetime import UTC, datetime

    b = service.BookingRequest(
        id=3, conversation_id=1, status="pending", created_at=datetime.now(UTC), phone="77010000001", name="Айгерим",
        payload={"date": "2026-11-27", "time": "15:00", "service": "Маникюр"},
    )
    text = service.admin_text(b)
    assert "Имя: «Айгерим»" in text and "Услуга: «Маникюр»" in text and "Когда: 2026-11-27 в 15:00" in text
    assert "Диалог:" not in text


async def test_non_admin_plus_message_goes_to_llm(env, pool):
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    cid = await _conv(pool)
    b = await service.create(cid, PAYLOAD)
    await handler.handle_incoming(make_msg(f"+{b.id}"))
    await env.idle()
    assert len(env.llm.calls) == 1
    assert await pool.fetchval("SELECT status FROM booking_requests") == "pending"
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте! Чем помочь?"]


async def test_booking_timeout_sends_followup_once(env, pool):
    await env.set_bot(booking_request_timeout_min=15, booking_timeout_message="Администратор скоро свяжется.")
    cid = await _conv(pool)
    b = await service.create(cid, PAYLOAD)
    fresh = await service.create(cid, PAYLOAD)
    await pool.execute("UPDATE booking_requests SET created_at = now() - interval '16 minutes' WHERE id = $1", b.id)
    assert await booking_timeout.check_once() == 1
    assert await booking_timeout.check_once() == 0
    assert env.gw.texts(CLIENT_JID) == ["Администратор скоро свяжется."]
    rows = {r["id"]: r for r in await pool.fetch("SELECT * FROM booking_requests")}
    assert rows[b.id]["timeout_notified"] and rows[b.id]["status"] == "pending"
    assert not rows[fresh.id]["timeout_notified"]


async def test_decide_twice_conflict(env, owner, pool):
    cid = await _conv(pool)
    b = await service.create(cid, PAYLOAD)
    r = await owner.post(f"/api/bookings/{b.id}/confirm", json={"note": "ждём"}, headers=csrf(owner))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed" and r.json()["decided_by"] == "Owner"
    r = await owner.post(f"/api/bookings/{b.id}/reject", json={}, headers=csrf(owner))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_decided"
    assert len(env.gw.texts(CLIENT_JID)) == 1
    with pytest.raises(service.BookingConflict):
        await service.decide(b.id, "rejected", None, "x")


async def test_bookings_api_list_filter_and_auth(env, owner, client, pool):
    cid = await _conv(pool)
    a = await service.create(cid, PAYLOAD)
    b = await service.create(cid, PAYLOAD)
    await service.decide(b.id, "rejected", "нет мест", "test")
    r = await owner.get("/api/bookings")
    assert r.status_code == 200
    items = r.json()
    assert [i["id"] for i in items] == [b.id, a.id]
    assert items[0]["phone"] == "77010000001" and items[0]["name"] == "Айдар"
    r = await owner.get("/api/bookings", params={"status": "pending"})
    assert [i["id"] for i in r.json()] == [a.id]
    assert (await owner.post(f"/api/bookings/{a.id}/confirm", json={})).status_code == 403  # no CSRF header
    assert (await owner.post("/api/bookings/9999/confirm", json={}, headers=csrf(owner))).status_code == 404
    owner.cookies.clear()
    assert (await owner.get("/api/bookings")).status_code == 401
