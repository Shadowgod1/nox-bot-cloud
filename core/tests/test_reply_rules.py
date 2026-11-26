"""Reply rules (new contacts only, exclusions, working-hours modes, phone takeover, pause) and lead
notifications (Telegram, test notification, write-only token)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from engine_fakes import ADMIN_JID, ADMIN_PHONE, CLIENT_JID, CLIENT_PHONE, env, make_msg  # noqa: F401
from httpx import AsyncClient
from pydantic import SecretStr

from iler import notify, reply_rules, settings_store
from iler.engine import admission, handler
from iler.settings_models import BotSettings, DayHours, NotifySettings, WeeklyHours
from iler.whatsapp import monitor
from iler.whatsapp.parse import history_contacts, message_timestamp, parse_upsert

OWNER = {"email": "owner@salon.kz", "password": "owner-pass-123", "name": "Owner"}
TG_TOKEN = "123456789:AAH-test_token_value_0123456789abcdef"
FRIEND = "77010000555"
FRIEND_JID = f"{FRIEND}@s.whatsapp.net"


def csrf(c: AsyncClient) -> dict:
    return {"X-ILER-CSRF": c.cookies.get("iler_csrf", "")}


@pytest.fixture
async def owner(client):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    return client


def _hours(open_: str, close: str) -> WeeklyHours:
    day = DayHours(open=open_, close=close)
    return WeeklyHours(always_open=False, mon=day, tue=day, wed=day, thu=day, fri=day, sat=day, sun=day)


def _conv(status="active", phone=CLIENT_PHONE, jid=CLIENT_JID):
    return {"status": status, "paused_until": None, "phone": phone, "chat_jid": jid}


# ---------- settings model ----------

def test_exclude_phones_are_normalized_and_deduplicated():
    bot = BotSettings(exclude_phones=["+7 (701) 000-05-55", "87010000555", " ", "77020000001"])
    assert bot.exclude_phones == [FRIEND, "77020000001"]
    with pytest.raises(ValueError):
        BotSettings(exclude_phones=["12345"])


def test_timezone_validated_and_defaults_to_almaty():
    assert BotSettings().timezone == "Asia/Almaty"
    with pytest.raises(ValueError):
        BotSettings(timezone="Mars/Olympus")


def test_operator_pause_defaults_to_12_hours():
    assert BotSettings().handover_pause_minutes == 12 * 60


# ---------- working hours modes (pure) ----------

def test_work_hours_mode_silent_skips_inside_working_hours():
    bot = BotSettings(hours=_hours("10:00", "20:00"), work_hours_mode="silent")
    now = datetime.now(UTC)
    inside = datetime(2026, 11, 26, 12, 0)
    outside = datetime(2026, 11, 26, 22, 0)
    assert admission.decide(_conv(), bot, now, inside).reason == "work_hours"
    assert admission.decide(_conv(), bot, now, outside).action == "reply"


def test_work_hours_only_is_silent_after_hours():
    bot = BotSettings(hours=_hours("10:00", "20:00"), after_hours_mode="silent")
    now = datetime.now(UTC)
    assert admission.decide(_conv(), bot, now, datetime(2026, 11, 26, 12, 0)).action == "reply"
    assert admission.decide(_conv(), bot, now, datetime(2026, 11, 26, 22, 0)).reason == "after_hours"


def test_silent_mode_without_hours_never_blocks():
    bot = BotSettings(work_hours_mode="silent")  # always_open: no working hours defined
    assert admission.decide(_conv(), bot, datetime.now(UTC), datetime(2026, 11, 26, 12, 0)).action == "reply"


def test_global_pause_skips():
    bot = BotSettings(enabled=False)
    assert admission.decide(_conv(), bot, datetime.now(UTC), datetime(2026, 11, 26, 12, 0)).reason == "bot_disabled"


# ---------- exclusions / new contacts (engine) ----------

async def test_excluded_number_is_stored_but_not_answered(env, pool):
    await env.set_bot(exclude_phones=["+7 701 000 00 01"])
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    assert env.gw.sent == [] and env.llm.calls == []
    assert await pool.fetchval("SELECT count(*) FROM messages WHERE author = 'client'") == 1


async def test_new_contacts_only_skips_known_and_answers_new(env, pool):
    await env.set_bot(new_contacts_only=True)
    await reply_rules.remember([(FRIEND, FRIEND_JID)], "history")
    await handler.handle_incoming(make_msg("Привет, подруга", phone=FRIEND))
    await handler.handle_incoming(make_msg("Здравствуйте, хочу записаться"))
    await env.idle()
    assert env.gw.texts(FRIEND_JID) == []
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте! Чем помочь?"]


async def test_known_contact_is_answered_when_rule_is_off(env, pool):
    await reply_rules.remember([(CLIENT_PHONE, CLIENT_JID)], "history")
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    assert len(env.gw.texts(CLIENT_JID)) == 1


async def test_history_message_is_never_answered_and_marks_contact_known(env, pool):
    await monitor.set_status("open")  # first open: the link starts now
    assert await reply_rules.linked_at() is not None
    msg = make_msg("Старое сообщение")
    msg.sent_at = datetime.now(UTC) - timedelta(days=3)
    await handler.handle_incoming(msg)
    await env.idle()
    assert env.gw.sent == []
    assert await pool.fetchval("SELECT count(*) FROM messages") == 0
    assert await reply_rules.is_known(CLIENT_PHONE, CLIENT_JID)
    live = make_msg("А сейчас?")
    live.sent_at = datetime.now(UTC)
    await handler.handle_incoming(live)
    await env.idle()
    assert len(env.gw.texts(CLIENT_JID)) == 1  # rule off: known contacts are still answered


async def test_logout_ends_the_link(pool, env):
    await monitor.set_status("open")
    await monitor.set_status("close", phone=None, last_error=None)
    assert await reply_rules.linked_at() is None
    await monitor.set_status("open")
    assert await reply_rules.linked_at() is not None


async def test_chat_started_from_phone_is_known(env, pool):
    await env.set_bot(new_contacts_only=True)
    await handler.handle_incoming(make_msg("Привет! Как дела?", phone=FRIEND, from_me=True))
    assert await reply_rules.is_known(FRIEND, FRIEND_JID)
    # an existing client chat does not become "known" when the operator answers in it
    await handler.handle_incoming(make_msg("Здравствуйте"))
    await env.idle()
    await handler.handle_incoming(make_msg("Сейчас подскажу", from_me=True))
    assert not await reply_rules.is_known(CLIENT_PHONE, CLIENT_JID)


async def test_webhook_history_sync_remembers_contacts(client, pool):
    payload = {
        "event": "messages.set", "instance": "iler",
        "data": {"messages": [
            {"key": {"remoteJid": FRIEND_JID, "fromMe": False, "id": "H1"}},
            {"key": {"remoteJid": "120363000000000009@g.us", "id": "H2"}},
            {"key": {"remoteJid": "status@broadcast", "id": "H3"}},
        ]},
    }
    r = await client.post("/internal/evolution/webhook", json=payload, headers={"apikey": "test-evo-key"})
    assert r.status_code == 200 and r.json()["known"] == 1
    chats = {"event": "chats.set", "instance": "iler", "data": [{"id": "77010000777@s.whatsapp.net"}]}
    r = await client.post("/internal/evolution/webhook", json=chats, headers={"apikey": "test-evo-key"})
    assert r.json()["known"] == 1
    assert await reply_rules.known_count() == 2


def test_parse_message_timestamp_shapes():
    assert message_timestamp(1764150000) == datetime.fromtimestamp(1764150000, UTC)
    assert message_timestamp("1764150000") == datetime.fromtimestamp(1764150000, UTC)
    assert message_timestamp({"low": 1764150000, "high": 0}) == datetime.fromtimestamp(1764150000, UTC)
    assert message_timestamp(None) is None
    msg = parse_upsert({"event": "messages.upsert", "data": {
        "key": {"remoteJid": CLIENT_JID, "id": "X1", "fromMe": False},
        "message": {"conversation": "hi"}, "messageTimestamp": 1764150000}})
    assert msg.sent_at == datetime.fromtimestamp(1764150000, UTC)
    assert history_contacts({"data": [{"remoteJid": "abc@lid"}]}) == [("", "abc@lid")]


# ---------- phone takeover ----------

async def test_phone_reply_pauses_for_configured_hours(env, pool):
    await env.set_bot(handover_pause_minutes=12 * 60)
    await handler.handle_incoming(make_msg("Здравствуйте"))
    await env.idle()
    await handler.handle_incoming(make_msg("Это администратор, сейчас помогу", from_me=True))
    conv = await pool.fetchrow("SELECT status, paused_until FROM conversations")
    assert conv["status"] == "paused"
    assert timedelta(hours=11, minutes=59) < conv["paused_until"] - datetime.now(UTC) <= timedelta(hours=12, seconds=5)
    env.gw.sent.clear()
    await handler.handle_incoming(make_msg("Спасибо"))
    await env.idle()
    assert env.gw.sent == []


# ---------- API: pause, known contacts ----------

async def test_pause_endpoint_changes_only_enabled(owner):
    await settings_store.put("bot", BotSettings(prompt="Салон", name="Айя"))
    r = await owner.post("/api/settings/bot/pause", json={"paused": True}, headers=csrf(owner))
    assert r.status_code == 200 and r.json() == {"enabled": False}
    bot = await settings_store.get("bot", BotSettings)
    assert bot.enabled is False and bot.prompt == "Салон" and bot.name == "Айя"
    assert (await owner.get("/api/settings/bot/pause")).json() == {"enabled": False}
    r = await owner.post("/api/settings/bot/pause", json={"paused": False}, headers=csrf(owner))
    assert r.json() == {"enabled": True}
    assert (await owner.post("/api/settings/bot/pause", json={"paused": True})).status_code == 403  # CSRF


async def test_known_contacts_count(owner):
    await reply_rules.remember([(FRIEND, FRIEND_JID), ("", "abc@lid")], "history")
    assert (await owner.get("/api/settings/bot/known-contacts")).json() == {"count": 2}


async def test_bot_settings_roundtrip_reply_rules(owner):
    body = (await owner.get("/api/settings/bot")).json()
    body |= {"new_contacts_only": True, "exclude_phones": ["8 701 000 05 55"], "work_hours_mode": "silent",
             "timezone": "Asia/Almaty"}
    r = await owner.put("/api/settings/bot", json=body, headers=csrf(owner))
    assert r.status_code == 200, r.text
    assert r.json()["exclude_phones"] == [FRIEND]
    body["timezone"] = "Nowhere/City"
    assert (await owner.put("/api/settings/bot", json=body, headers=csrf(owner))).status_code == 422


# ---------- lead notifications ----------

class _Tg:
    """MockTransport for the Telegram Bot API; records calls."""

    def __init__(self, status=200, body=None):
        self.calls: list[tuple[str, dict]] = []
        self.status = status
        self.body = body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((method, json.loads(request.content or b"{}")))
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 1, "message": {"chat": {"id": 555, "first_name": "Айгерим", "type": "private"}}},
            ]})
        return httpx.Response(200, json={"ok": True, "result": {}})


@pytest.fixture
def tg():
    t = _Tg()
    notify.set_transport(httpx.MockTransport(t))
    yield t
    notify.set_transport(None)


async def test_telegram_token_is_write_only(owner):
    body = (await owner.get("/api/settings/notify")).json()
    assert body["telegram_bot_token"] is None
    body |= {"telegram_bot_token": TG_TOKEN, "telegram_chat_ids": ["555"]}
    r = await owner.put("/api/settings/notify", json=body, headers=csrf(owner))
    assert r.status_code == 200, r.text
    assert r.json()["telegram_bot_token"] == "••••"
    got = (await owner.get("/api/settings/notify")).json()
    assert got["telegram_bot_token"] == "••••" and TG_TOKEN[-4:] not in json.dumps(got)
    # saving the masked value keeps the stored token
    r = await owner.put("/api/settings/notify", json=got, headers=csrf(owner))
    stored = await settings_store.get("notify", NotifySettings)
    assert stored.telegram_bot_token.get_secret_value() == TG_TOKEN
    raw = await (await _pool()).fetchval("SELECT value::text FROM settings WHERE key = 'notify'")
    assert TG_TOKEN not in raw


async def _pool():
    from iler.db.pool import get_pool

    return get_pool()


async def test_telegram_chats_lookup(owner, tg):
    r = await owner.post("/api/settings/notify/telegram/chats", json={"token": TG_TOKEN}, headers=csrf(owner))
    assert r.json() == {"ok": True, "chats": [{"id": "555", "title": "Айгерим"}]}
    r = await owner.post("/api/settings/notify/telegram/chats", json={"token": "bad"}, headers=csrf(owner))
    assert r.json()["ok"] is False and "bad" not in r.json()["error"]


async def test_telegram_bad_token_error_does_not_leak_token(owner):
    t = _Tg(status=401, body={"ok": False, "description": "Unauthorized"})
    notify.set_transport(httpx.MockTransport(t))
    try:
        r = await owner.post("/api/settings/notify/telegram/chats", json={"token": TG_TOKEN}, headers=csrf(owner))
    finally:
        notify.set_transport(None)
    assert r.json()["ok"] is False and TG_TOKEN not in r.text


async def test_notify_test_sends_to_admin_whatsapp_and_telegram_only(owner, env, tg):
    await settings_store.put("notify", NotifySettings(
        admin_phones=[ADMIN_PHONE], telegram_bot_token=SecretStr(TG_TOKEN), telegram_chat_ids=["555"],
        admin_url="https://bot.example.kz",
    ))
    r = await owner.post("/api/settings/notify/test", headers=csrf(owner))
    assert r.status_code == 200, r.text
    assert r.json()["whatsapp"]["ok"] and r.json()["telegram"]["ok"]
    assert [j for j, _ in env.gw.sent] == [ADMIN_JID]  # nothing goes to clients
    assert "https://bot.example.kz/conversations" in env.gw.sent[0][1]
    assert tg.calls[0][0] == "sendMessage" and tg.calls[0][1]["chat_id"] == "555"


async def test_notify_test_without_channels_reports_nothing(owner, env):
    r = await owner.post("/api/settings/notify/test", headers=csrf(owner))
    assert r.json() == {"whatsapp": None, "telegram": None}


async def test_booking_lead_goes_to_telegram_once_with_link(env, pool, tg):
    await env.set_notify(admin_phones=[ADMIN_PHONE], telegram_bot_token=SecretStr(TG_TOKEN),
                         telegram_chat_ids=["555"], admin_url="https://bot.example.kz")
    from iler.bookings import service

    cid = await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone, name) VALUES ($1, $2, 'Айгерим') RETURNING id",
        CLIENT_JID, CLIENT_PHONE,
    )
    b = await service.create(cid, {"date": "2026-11-27", "time": "15:00", "service": "Маникюр"})
    await service._announce(b)  # a repeated announce (retry) must not notify twice
    sends = [c for c in tg.calls if c[0] == "sendMessage"]
    assert len(sends) == 1
    text = sends[0][1]["text"]
    assert "Имя: «Айгерим»" in text and "Телефон: +7 701 000 00 01" in text and "Услуга: «Маникюр»" in text
    assert f"Диалог: https://bot.example.kz/conversations/{cid}" in text
    assert len(env.gw.texts(ADMIN_JID)) == 1 and env.gw.texts(CLIENT_JID) == []


async def test_telegram_failure_never_breaks_admin_notification(env, pool):
    t = _Tg(status=400, body={"ok": False, "description": "Bad Request: chat not found"})
    notify.set_transport(httpx.MockTransport(t))
    try:
        await env.set_notify(admin_phones=[ADMIN_PHONE], telegram_bot_token=SecretStr(TG_TOKEN),
                             telegram_chat_ids=["1"])
        from iler.engine import sender

        statuses = await sender.notify_admins("Нужен оператор", key="k1")
    finally:
        notify.set_transport(None)
    assert statuses == ["sent", "failed"]
    assert (await pool.fetchval("SELECT error FROM notification_log")).startswith("чат не найден")
