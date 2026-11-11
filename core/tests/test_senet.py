import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from iler import settings_store
from iler.auth import deps
from iler.errors import ApiError
from iler.settings_models import AllSettings, BotSettings, LlmSettings, NotifySettings, SenetSettings
from iler.tools import registry
from iler.tools.base import ToolContext
from iler.tools.senet import client as senet_client
from iler.tools.senet import tool as senet_tool
from iler.tools.senet.client import SenetClient, SenetError
from iler.tools.senet.mapping import extract_items, to_workstation

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "senet_workstations.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clear_senet_cache():
    SenetClient.clear_cache()
    yield
    SenetClient.clear_cache()


def _all(senet: SenetSettings) -> AllSettings:
    return AllSettings(bot=BotSettings(), llm=LlmSettings(), notify=NotifySettings(), senet=senet)


def _ctx(senet: SenetSettings | None = None) -> ToolContext:
    extra = {"senet": senet} if senet is not None else {}
    return ToolContext(conversation_id=None, phone="77010000000", settings=BotSettings(), now=datetime.now(), extra=extra)


def _mock(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


CONFIGURED = SenetSettings(enabled=True, orgname="club1", app_key=SecretStr("abc"))


# --- tool enablement -------------------------------------------------------

def test_tool_registered():
    assert registry.get("senet_free_workstations") is senet_tool.TOOL


def test_tool_disabled_without_key():
    t = senet_tool.TOOL
    assert not t.enabled(_all(SenetSettings()))
    assert not t.enabled(_all(SenetSettings(enabled=True, orgname="club1")))
    assert not t.enabled(_all(SenetSettings(enabled=True, app_key=SecretStr("abc"))))
    assert not t.enabled(_all(SenetSettings(enabled=False, orgname="club1", app_key=SecretStr("abc"))))
    assert t.enabled(_all(CONFIGURED))


# --- client ----------------------------------------------------------------

async def test_auth_header_format():
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[])

    c = SenetClient("club1", "abc", transport=_mock(handler))
    assert await c.list_workstations() == []
    assert seen["auth"] == "key abc"
    assert seen["url"] == "https://club1.api.enes.tech/workstations/"


async def test_cache_30s():
    calls = []
    now = [1000.0]

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=FIXTURE)

    c = SenetClient("club1", "abc", transport=_mock(handler), clock=lambda: now[0])
    first = await c.list_workstations()
    now[0] += 29
    second = await SenetClient("club1", "abc", transport=_mock(handler), clock=lambda: now[0]).list_workstations()
    assert len(calls) == 1
    assert first == second and len(first) == 10
    now[0] += 2
    await c.list_workstations()
    assert len(calls) == 2
    await c.list_workstations(use_cache=False)
    assert len(calls) == 3


async def test_client_http_error_raises_senet_error():
    c = SenetClient("club1", "abc", transport=_mock(lambda r: httpx.Response(401, json={"detail": "bad key"})))
    with pytest.raises(SenetError) as e:
        await c.list_workstations()
    assert "abc" not in str(e.value)


async def test_client_timeout_raises_senet_error():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    c = SenetClient("club1", "abc", transport=_mock(handler))
    with pytest.raises(SenetError):
        await c.list_workstations()


def test_client_rejects_bad_orgname():
    for bad in ["", "evil.com/x", "a b", "club1.example", "../x"]:
        with pytest.raises(SenetError):
            SenetClient(bad, "abc")


# --- tool run --------------------------------------------------------------

async def test_timeout_fail_open_returns_error_text(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(senet_tool, "make_client", lambda s: SenetClient(s.orgname, s.app_key.get_secret_value(), transport=_mock(handler)))
    res = await senet_tool.TOOL.run(_ctx(CONFIGURED), {})
    assert res.content == "error: SENET недоступен, предложи оформить заявку через create_booking_request"
    assert res.user_visible is False


async def test_tool_returns_free_by_zone(monkeypatch):
    monkeypatch.setattr(
        senet_tool,
        "make_client",
        lambda s: SenetClient(s.orgname, s.app_key.get_secret_value(), transport=_mock(lambda r: httpx.Response(200, json=FIXTURE))),
    )
    res = await senet_tool.TOOL.run(_ctx(CONFIGURED), {})
    assert res.content["total_free"] == 5
    assert res.content["by_zone"] == {"Общий зал": 3, "VIP": 1, "PlayStation": 1}
    assert {"name": "VIP-01", "zone": "VIP"} in res.content["free"]

    res = await senet_tool.TOOL.run(_ctx(CONFIGURED), {"zone": "vip"})
    assert res.content["total_free"] == 1
    assert res.content["free"] == [{"name": "VIP-01", "zone": "VIP"}]


async def test_tool_reads_settings_from_store(pool, monkeypatch):
    await settings_store.put("senet", CONFIGURED)
    seen = {}

    def factory(s):
        seen["org"] = s.orgname
        return SenetClient(s.orgname, s.app_key.get_secret_value(), transport=_mock(lambda r: httpx.Response(200, json=[])))

    monkeypatch.setattr(senet_tool, "make_client", factory)
    res = await senet_tool.TOOL.run(_ctx(), {})
    assert seen["org"] == "club1"
    assert res.content == {"free": [], "total_free": 0, "by_zone": {}}


# --- mapping ---------------------------------------------------------------

def test_mapping_unknown_fields_state_unknown():
    ws = to_workstation({"foo": 1, "bar": "baz"})
    assert ws.state == "unknown"
    assert ws.zone is None
    assert isinstance(ws.name, str) and ws.name
    assert to_workstation({"id": 3, "online": True}).state == "unknown"
    assert to_workstation({"name": "X", "state": "weird-value"}).state == "unknown"


def test_mapping_variants():
    assert to_workstation({"number": 7}).name == "7"
    assert to_workstation({"zone": {"title": "VIP"}}).zone == "VIP"
    assert to_workstation({"is_online": False, "state": "free"}).state == "offline"
    assert to_workstation({"reserved": True}).state == "reserved"
    assert to_workstation({"status": "In use"}).state == "busy"


def test_extract_items_shapes():
    assert extract_items([{"a": 1}, "junk"]) == [{"a": 1}]
    for key in ("results", "items", "data"):
        assert extract_items({key: [{"a": 1}]}) == [{"a": 1}]
    assert extract_items({"data": {"results": [{"a": 1}]}}) == [{"a": 1}]
    assert extract_items({"unexpected": True}) == []
    assert extract_items(None) == []


def test_mapping_fixture_counts_by_zone():
    stations = [to_workstation(r) for r in extract_items(FIXTURE)]
    assert len(stations) == 10
    states = {s.name: s.state for s in stations}
    assert states == {
        "PC-01": "free", "PC-02": "busy", "PC-03": "free", "4": "free", "PC-05": "offline",
        "VIP-01": "free", "VIP-02": "reserved", "VIP-03": "busy", "PS5-01": "free", "PS5-02": "busy",
    }
    free_by_zone: dict[str, int] = {}
    for s in stations:
        if s.state == "free":
            free_by_zone[s.zone] = free_by_zone.get(s.zone, 0) + 1
    assert free_by_zone == {"Общий зал": 3, "VIP": 1, "PlayStation": 1}


# --- API -------------------------------------------------------------------

class _User:
    def __init__(self, role):
        self.id, self.email, self.name, self.role = 1, "o@x", "O", role


@pytest.fixture
def as_owner(app):
    async def owner():
        return _User("owner")

    async def ok():
        return None

    app.dependency_overrides[deps.current_user] = owner
    app.dependency_overrides[deps.require_owner] = owner
    app.dependency_overrides[deps.csrf_protect] = ok
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def as_operator(app):
    async def operator():
        return _User("operator")

    async def forbidden():
        raise ApiError(403, "forbidden")

    async def ok():
        return None

    app.dependency_overrides[deps.current_user] = operator
    app.dependency_overrides[deps.require_owner] = forbidden
    app.dependency_overrides[deps.csrf_protect] = ok
    yield
    app.dependency_overrides.clear()


async def test_api_requires_auth(client):
    r = await client.get("/api/integrations/senet")
    assert r.status_code == 401


async def test_api_get_masks_key(client, as_owner):
    await settings_store.put("senet", SenetSettings(enabled=True, orgname="club1", app_key=SecretStr("secret-key-1234")))
    r = await client.get("/api/integrations/senet")
    assert r.status_code == 200
    body = r.json()
    assert body == {"enabled": True, "orgname": "club1", "app_key": "••••1234"}
    assert "secret-key" not in r.text


async def test_api_get_empty(client, as_owner):
    r = await client.get("/api/integrations/senet")
    assert r.json() == {"enabled": False, "orgname": None, "app_key": None}


async def test_api_put_keeps_masked_key(client, as_owner):
    await settings_store.put("senet", SenetSettings(enabled=False, orgname="club1", app_key=SecretStr("secret-key-1234")))
    r = await client.put("/api/integrations/senet", json={"enabled": True, "orgname": "Club2", "app_key": "••••1234"})
    assert r.status_code == 200
    assert r.json()["app_key"] == "••••1234"
    stored = await settings_store.get("senet", SenetSettings)
    assert stored.enabled is True and stored.orgname == "club2"
    assert stored.app_key.get_secret_value() == "secret-key-1234"

    r = await client.put("/api/integrations/senet", json={"enabled": True, "orgname": "club2", "app_key": "new-key-9999"})
    stored = await settings_store.get("senet", SenetSettings)
    assert stored.app_key.get_secret_value() == "new-key-9999"

    r = await client.put("/api/integrations/senet", json={"enabled": False, "orgname": "club2", "app_key": ""})
    stored = await settings_store.get("senet", SenetSettings)
    assert stored.app_key is None


async def test_api_put_rejects_bad_orgname(client, as_owner):
    r = await client.put("/api/integrations/senet", json={"enabled": True, "orgname": "evil.com/x", "app_key": "k"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


async def test_api_put_and_test_owner_only(client, as_operator):
    r = await client.get("/api/integrations/senet")
    assert r.status_code == 200
    r = await client.put("/api/integrations/senet", json={"enabled": True, "orgname": "club1", "app_key": "k"})
    assert r.status_code == 403
    r = await client.post("/api/integrations/senet/test")
    assert r.status_code == 403


async def test_api_test_ok(client, as_owner, monkeypatch):
    await settings_store.put("senet", CONFIGURED)
    monkeypatch.setattr(
        senet_client, "_default_transport", lambda: _mock(lambda r: httpx.Response(200, json=FIXTURE))
    )
    r = await client.post("/api/integrations/senet/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 10}


async def test_api_test_error(client, as_owner, monkeypatch):
    await settings_store.put("senet", CONFIGURED)
    monkeypatch.setattr(senet_client, "_default_transport", lambda: _mock(lambda r: httpx.Response(403, json={})))
    r = await client.post("/api/integrations/senet/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["error"]


async def test_api_test_not_configured(client, as_owner):
    r = await client.post("/api/integrations/senet/test")
    assert r.json() == {"ok": False, "error": "not_configured"}
