"""Evolution gateway, parser, connection monitor and /api/whatsapp/* (Task 4)."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from iler import events
from iler.auth import deps as auth_deps
from iler.config import get_settings
from iler.whatsapp import monitor, set_gateway
from iler.whatsapp.evolution import EvolutionError, EvolutionGateway, SendUncertain, WrongState
from iler.whatsapp.parse import parse_upsert


class FakeEvolution:
    """Scriptable Evolution API v2 behind httpx.MockTransport."""

    def __init__(self, state: str = "close", instance_exists: bool = True):
        self.state = state
        self.instance_exists = instance_exists
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.send_status = 201
        self.send_exc: Exception | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def paths(self, method: str | None = None) -> list[str]:
        return [p for m, p, _, _ in self.calls if method is None or m == method]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body, dict(request.url.params)))
        if request.headers.get("apikey") != "test-evo-key":
            return httpx.Response(401, json={"error": "Unauthorized"})
        if path == "/instance/connectionState/iler":
            if not self.instance_exists:
                return httpx.Response(404, json={"status": 404, "error": "Not Found"})
            return httpx.Response(200, json={"instance": {"instanceName": "iler", "state": self.state}})
        if path == "/instance/create" and request.method == "POST":
            self.instance_exists = True
            return httpx.Response(201, json={"instance": {"instanceName": "iler", "status": "created"}})
        if path == "/webhook/set/iler":
            return httpx.Response(201, json={"webhook": body["webhook"]})
        if path == "/instance/connect/iler":
            if "number" in request.url.params:
                return httpx.Response(200, json={"pairingCode": "WZYEH1YY", "code": "2@x", "count": 1})
            self.state = "connecting"
            return httpx.Response(200, json={"code": "2@x", "base64": "data:image/png;base64,QUJD", "count": 1})
        if path == "/instance/logout/iler" and request.method == "DELETE":
            self.state = "close"
            return httpx.Response(200, json={"status": "SUCCESS", "error": False})
        if path == "/instance/delete/iler":
            self.instance_exists = False
            return httpx.Response(200, json={"status": "SUCCESS"})
        if path == "/message/sendText/iler":
            if self.send_exc is not None:
                raise self.send_exc
            if self.send_status >= 400:
                return httpx.Response(self.send_status, json={"status": self.send_status, "error": "boom"})
            return httpx.Response(201, json={"key": {"remoteJid": body["number"], "fromMe": True, "id": "BAE5OUT1"}})
        if path == "/chat/sendPresence/iler":
            return httpx.Response(201, json={"presence": body["presence"]})
        return httpx.Response(404, json={"error": "unknown route"})


@pytest.fixture
def evo():
    fake = FakeEvolution()
    gw = EvolutionGateway(get_settings(), transport=fake.transport())
    set_gateway(gw)
    yield fake, gw
    set_gateway(None)


@pytest.fixture
def authed(app):
    async def _user():
        return auth_deps.User(id=1, email="o@example.com", name="Owner", role="owner")

    async def _noop():
        return None

    app.dependency_overrides[auth_deps.current_user] = _user
    app.dependency_overrides[auth_deps.csrf_protect] = _noop
    yield
    app.dependency_overrides.pop(auth_deps.current_user, None)
    app.dependency_overrides.pop(auth_deps.csrf_protect, None)


async def _set_status(pool, status: str, failures: int = 0):
    await pool.execute(
        "INSERT INTO wa_instance(id, evo_name, status, send_failures) VALUES (1, 'iler', $1, $2)"
        " ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, send_failures = EXCLUDED.send_failures",
        status,
        failures,
    )


# ---------- parser ----------

def _upsert(remote_jid="77010000001@s.whatsapp.net", msg=None, from_me=False, mid="3EB0AAA", **key_extra):
    return {
        "event": "messages.upsert",
        "instance": "iler",
        "data": {
            "key": {"remoteJid": remote_jid, "fromMe": from_me, "id": mid, **key_extra},
            "pushName": "Айдар",
            "message": msg if msg is not None else {"conversation": "Привет"},
            "messageType": "conversation",
            "messageTimestamp": 1_700_000_000,
        },
    }


def test_parse_text_message():
    received = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
    m = parse_upsert(_upsert(), received_at=received)
    assert m.evo_id == "3EB0AAA" and m.jid == "77010000001@s.whatsapp.net" and m.phone == "77010000001"
    assert m.text == "Привет" and m.push_name == "Айдар" and not m.from_me and not m.is_group
    assert m.media is None
    assert m.ts == received  # our own receive time, not the Evolution timestamp


def test_parse_extended_text_and_image_caption():
    m = parse_upsert(_upsert(msg={"extendedTextMessage": {"text": "есть места?"}}))
    assert m.text == "есть места?"
    m = parse_upsert(_upsert(msg={"imageMessage": {"caption": "вот", "mimetype": "image/jpeg"}}))
    assert m.text == "вот" and m.media["type"] == "image"
    m = parse_upsert(_upsert(msg={"ephemeralMessage": {"message": {"conversation": "скрытое"}}}))
    assert m.text == "скрытое"


def test_parse_lid_uses_phone_alt():
    m = parse_upsert(_upsert(remote_jid="123456789@lid", remoteJidAlt="77010000002@s.whatsapp.net"))
    assert m.phone == "77010000002" and m.jid == "77010000002@s.whatsapp.net"


def test_parse_skips_status_reactions_and_other_events():
    assert parse_upsert(_upsert(remote_jid="status@broadcast")) is None
    assert parse_upsert(_upsert(msg={"reactionMessage": {"text": "👍"}})) is None
    assert parse_upsert(_upsert(msg={"protocolMessage": {"type": 0}})) is None
    assert parse_upsert({"event": "connection.update", "data": {"instance": "iler", "state": "open"}}) is None
    assert parse_upsert({"event": "messages.upsert", "data": {}}) is None


def test_parse_group_message_flagged():
    m = parse_upsert(_upsert(remote_jid="120363000000@g.us", participant="77010000003@s.whatsapp.net"))
    assert m.is_group and m.jid == "120363000000@g.us" and m.phone == "77010000003"


# ---------- gateway ----------

async def test_gateway_send_text_returns_evo_id(evo, pool):
    fake, gw = evo
    assert await gw.send_text("77010000001@s.whatsapp.net", "hi") == "BAE5OUT1"
    method, path, body, _ = fake.calls[-1]
    assert (method, path) == ("POST", "/message/sendText/iler")
    assert body == {"number": "77010000001@s.whatsapp.net", "text": "hi"}


async def test_gateway_send_timeout_is_uncertain(evo, pool):
    fake, gw = evo
    fake.send_exc = httpx.ReadTimeout("slow")
    with pytest.raises(SendUncertain):
        await gw.send_text("77010000001@s.whatsapp.net", "hi")


async def test_gateway_presence_best_effort(evo):
    fake, gw = evo
    await gw.send_presence("77010000001@s.whatsapp.net", "composing", 1200)
    _, path, body, _ = fake.calls[-1]
    assert path == "/chat/sendPresence/iler"
    assert body == {"number": "77010000001@s.whatsapp.net", "presence": "composing", "delay": 1200}


async def test_ensure_instance_creates_when_missing_and_sets_webhook(evo):
    fake, gw = evo
    fake.instance_exists = False
    await gw.ensure_instance("http://iler-core:8000/internal/evolution/webhook")
    create = next(c for c in fake.calls if c[1] == "/instance/create")
    assert create[2]["integration"] == "WHATSAPP-BAILEYS" and create[2]["qrcode"] is True
    hook = next(c for c in fake.calls if c[1] == "/webhook/set/iler")[2]["webhook"]
    assert hook["url"].endswith("/internal/evolution/webhook")
    assert hook["headers"] == {"apikey": "test-evo-key"}
    assert set(hook["events"]) == {"MESSAGES_UPSERT", "CONNECTION_UPDATE"}


async def test_ensure_instance_never_recreates_open_instance(evo):
    fake, gw = evo
    fake.state = "open"
    await gw.ensure_instance("http://x/internal/evolution/webhook")
    assert "/instance/create" not in fake.paths()
    assert not any("delete" in p for p in fake.paths())


async def test_connect_returns_qr(evo):
    fake, gw = evo
    assert await gw.connect() == {"qr_base64": "data:image/png;base64,QUJD"}


async def test_pairing_wrong_state_in_gateway(evo):
    fake, gw = evo
    fake.state = "open"
    with pytest.raises(WrongState):
        await gw.pairing_code("77010000001")
    fake.state = "close"
    assert await gw.pairing_code("+7 701 000 00 01") == "WZYEH1YY"
    assert fake.calls[-1][3] == {"number": "77010000001"}


# ---------- monitor ----------

async def test_send_failure_marks_degraded(evo, pool):
    fake, gw = evo
    await _set_status(pool, "open")
    fake.send_status = 500
    async with events.subscription() as q:
        for _ in range(3):
            with pytest.raises(EvolutionError):
                await gw.send_text("77010000001@s.whatsapp.net", "hi")
        row = await pool.fetchrow("SELECT status, send_failures, last_error FROM wa_instance WHERE id=1")
        assert row["status"] == "degraded" and row["send_failures"] == 3 and row["last_error"]
        evs = []
        while not q.empty():
            evs.append(q.get_nowait())
    assert any(e["type"] == "whatsapp.status" and e["data"]["status"] == "degraded" for e in evs)

    # connectionState still reports open (half-open session): the monitor must keep it degraded
    fake.state = "open"
    await monitor.check_once(gw)
    assert await pool.fetchval("SELECT status FROM wa_instance WHERE id=1") == "degraded"

    # a successful send clears the degraded flag
    fake.send_status = 201
    await gw.send_text("77010000001@s.whatsapp.net", "hi")
    row = await pool.fetchrow("SELECT status, send_failures FROM wa_instance WHERE id=1")
    assert row["status"] == "open" and row["send_failures"] == 0


async def test_client_error_does_not_count_as_send_failure(evo, pool):
    fake, gw = evo
    await _set_status(pool, "open")
    fake.send_status = 400
    for _ in range(3):
        with pytest.raises(EvolutionError):
            await gw.send_text("77010000001@s.whatsapp.net", "hi")
    assert await pool.fetchval("SELECT status FROM wa_instance WHERE id=1") == "open"


async def test_monitor_tracks_state_and_alerts_after_5_minutes(evo, pool, monkeypatch):
    fake, gw = evo
    fake.state = "open"
    await monitor.check_once(gw)
    row = await pool.fetchrow("SELECT status, last_seen_at FROM wa_instance WHERE id=1")
    assert row["status"] == "open" and row["last_seen_at"] is not None

    sent = []

    async def fake_alert(text):
        sent.append(text)

    monkeypatch.setattr(monitor, "send_alert", fake_alert)
    fake.state = "close"
    async with events.subscription() as q:
        await monitor.check_once(gw)
        assert await pool.fetchval("SELECT status FROM wa_instance WHERE id=1") == "close"
        assert sent == []  # not yet 5 minutes
        await pool.execute("UPDATE wa_instance SET last_seen_at = now() - interval '6 minutes'")
        await monitor.check_once(gw)
        await monitor.check_once(gw)  # alert only once
        evs = [q.get_nowait() for _ in range(q.qsize())]
    assert len(sent) == 1
    assert any(e["type"] == "whatsapp.status" and e["data"].get("alert") for e in evs)


async def test_monitor_evolution_unreachable(pool):
    def boom(request):
        raise httpx.ConnectError("refused")

    gw = EvolutionGateway(get_settings(), transport=httpx.MockTransport(boom))
    await monitor.check_once(gw)
    row = await pool.fetchrow("SELECT status, last_error FROM wa_instance WHERE id=1")
    assert row["status"] == "close" and "unreachable" in row["last_error"]


# ---------- API ----------

async def test_status_requires_auth(client):
    r = await client.get("/api/whatsapp/status")
    assert r.status_code == 401


async def test_status_endpoint(client, pool, authed, evo):
    await pool.execute(
        "INSERT INTO wa_instance(id, evo_name, phone, status, last_error) VALUES (1, 'iler', '77010000009', 'open', NULL)"
    )
    r = await client.get("/api/whatsapp/status")
    assert r.status_code == 200
    assert r.json() == {"status": "open", "phone": "77010000009", "last_error": None}


async def test_status_endpoint_without_row(client, authed, evo):
    r = await client.get("/api/whatsapp/status")
    assert r.status_code == 200 and r.json()["status"] == "close"


async def test_connect_endpoint_returns_qr(client, pool, authed, evo):
    fake, _ = evo
    fake.instance_exists = False
    r = await client.post("/api/whatsapp/connect")
    assert r.status_code == 200
    assert r.json() == {"qr_base64": "data:image/png;base64,QUJD"}
    assert "/instance/create" in fake.paths()
    assert await pool.fetchval("SELECT status FROM wa_instance WHERE id=1") == "connecting"


async def test_connect_when_open_conflicts_and_keeps_instance(client, authed, evo):
    fake, _ = evo
    fake.state = "open"
    r = await client.post("/api/whatsapp/connect")
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_connected"
    assert "/instance/connect/iler" not in fake.paths()
    assert not any("delete" in p or "logout" in p for p in fake.paths())


async def test_pairing_only_from_close_state(client, authed, evo):
    fake, _ = evo
    fake.state = "open"
    r = await client.post("/api/whatsapp/pairing", json={"phone": "+7 701 000 00 01"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "wrong_state"
    assert fake.instance_exists
    assert not any("delete" in p or "logout" in p or "create" in p for p in fake.paths())
    assert not any(c[1] == "/instance/connect/iler" for c in fake.calls)

    fake.state = "close"
    r = await client.post("/api/whatsapp/pairing", json={"phone": "+7 701 000 00 01"})
    assert r.status_code == 200 and r.json() == {"code": "WZYEH1YY"}


async def test_pairing_rejects_bad_phone(client, authed, evo):
    r = await client.post("/api/whatsapp/pairing", json={"phone": "12"})
    assert r.status_code == 422


async def test_logout_endpoint(client, pool, authed, evo):
    fake, _ = evo
    fake.state = "open"
    await pool.execute("INSERT INTO wa_instance(id, evo_name, phone, status) VALUES (1, 'iler', '77010000009', 'open')")
    async with events.subscription() as q:
        r = await client.post("/api/whatsapp/logout")
        evs = [q.get_nowait() for _ in range(q.qsize())]
    assert r.status_code == 200
    assert ("DELETE", "/instance/logout/iler") in [(m, p) for m, p, _, _ in fake.calls]
    assert not any(p == "/instance/delete/iler" for p in fake.paths())
    row = await pool.fetchrow("SELECT status, phone FROM wa_instance WHERE id=1")
    assert row["status"] == "close" and row["phone"] is None
    assert any(e["type"] == "whatsapp.status" for e in evs)


async def test_evolution_down_maps_to_502(client, authed):
    def boom(request):
        raise httpx.ConnectError("refused")

    set_gateway(EvolutionGateway(get_settings(), transport=httpx.MockTransport(boom)))
    try:
        r = await client.post("/api/whatsapp/connect")
    finally:
        set_gateway(None)
    assert r.status_code == 502 and r.json()["error"]["code"] == "evolution_unavailable"


@pytest.fixture
def as_operator(app):
    async def _user():
        return auth_deps.User(id=2, email="op@example.com", name="Op", role="operator")

    async def _noop():
        return None

    app.dependency_overrides[auth_deps.current_user] = _user
    app.dependency_overrides[auth_deps.csrf_protect] = _noop
    yield
    app.dependency_overrides.pop(auth_deps.current_user, None)
    app.dependency_overrides.pop(auth_deps.csrf_protect, None)


async def test_operator_cannot_connect_pair_or_logout(client, as_operator, evo):
    fake, _ = evo
    assert (await client.get("/api/whatsapp/status")).status_code == 200
    assert (await client.post("/api/whatsapp/connect")).status_code == 403
    assert (await client.post("/api/whatsapp/pairing", json={"phone": "77010000009"})).status_code == 403
    assert (await client.post("/api/whatsapp/logout")).status_code == 403
    assert fake.calls == []


# ---------- SMTP alerts ----------

class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.context = host, port, context
        self.tls_context = None
        self.started_tls = False
        self.logged_in = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.started_tls = True
        self.tls_context = context

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def smtp_fakes(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(monitor.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(monitor.smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP.instances


async def _notify(**smtp):
    from pydantic import SecretStr

    from iler import settings_store
    from iler.settings_models import NotifySettings, SmtpSettings

    base = {"host": "smtp.example.org", "user": "bot", "password": SecretStr("pw-12345678"), "to": ["a@b.kz"]}
    await settings_store.put("notify", NotifySettings(smtp=SmtpSettings(**(base | smtp))))


async def test_smtp_starttls_uses_verified_context(pool, smtp_fakes):
    import ssl

    await _notify(port=587, starttls=True)
    await monitor.send_alert("down")
    [s] = smtp_fakes
    assert type(s) is FakeSMTP and s.started_tls
    assert isinstance(s.tls_context, ssl.SSLContext) and s.tls_context.verify_mode == ssl.CERT_REQUIRED
    assert s.logged_in == ("bot", "pw-12345678") and len(s.sent) == 1


async def test_smtp_ssl_on_port_465(pool, smtp_fakes):
    import ssl

    await _notify(port=465, starttls=False)
    await monitor.send_alert("down")
    [s] = smtp_fakes
    assert type(s) is FakeSMTPSSL and isinstance(s.context, ssl.SSLContext)
    assert not s.started_tls and s.logged_in == ("bot", "pw-12345678")


async def test_smtp_never_logs_in_without_tls(pool, smtp_fakes):
    await _notify(port=25, starttls=False)
    await monitor.send_alert("down")
    [s] = smtp_fakes
    assert type(s) is FakeSMTP and not s.started_tls
    assert s.logged_in is None and len(s.sent) == 1


async def test_send_alert_custom_subject(pool, smtp_fakes):
    await _notify(port=587)
    await monitor.send_alert("backup failed", subject="ILER Bot: backup failed")
    assert smtp_fakes[0].sent[0]["Subject"] == "ILER Bot: backup failed"
