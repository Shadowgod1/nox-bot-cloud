"""LLM gateway mode: clients, providers list, 402 / 401 handover, balance cache, alerts. No network:
providers talk to httpx / httpx2 MockTransports and the balance endpoint to an httpx MockTransport."""

import json
import time
from datetime import UTC, datetime

import httpx
import httpx2
import pytest
from engine_fakes import ADMIN_JID, ADMIN_PHONE, CLIENT_JID, env, make_msg  # noqa: F401

from iler import settings_store, usage
from iler.config import get_settings
from iler.engine import handler, sender
from iler.llm import gateway, router
from iler.llm.anthropic_provider import AnthropicProvider
from iler.llm.base import ChatMessage, LlmError
from iler.llm.fake import FakeLLM
from iler.llm.gemini_provider import GeminiProvider
from iler.llm.openai_provider import OpenAIProvider
from iler.settings_models import BotSettings, LlmSettings
from iler.tools.base import ToolContext

GW = "http://gw.test"
VK = "nox_vk_test_0123456789abcdef"
HOLD = "Спасибо за сообщение! Сейчас подключится менеджер и ответит вам."
OWNER = {"email": "owner@club.kz", "password": "owner-pass-123", "name": "Owner"}
MSGS = [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="hi")]


class BalanceApi:
    """Fake GET /llm/v1/balance: returns ``self.body`` with ``self.status`` and records every request."""

    def __init__(self, coins_left=5000, monthly_limit=10000, status="active"):
        self.status = 200
        self.body: dict = {"coins_left": coins_left, "monthly_limit": monthly_limit,
                           "period_end": "2026-12-01T00:00:00+05:00", "status": status}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    def set(self, **kw):
        self.body.update(kw)


def expire_balance_cache():
    gateway._state.attempt_mono = time.monotonic() - gateway.BALANCE_TTL_S - 1


@pytest.fixture
def balance_api():
    return BalanceApi()


@pytest.fixture
def gw_mode(monkeypatch, balance_api):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "llm_gateway_url", GW + "/")
    monkeypatch.setattr(cfg, "llm_gateway_key", VK)
    for name in ("openai", "gemini", "anthropic"):
        monkeypatch.setattr(cfg, f"{name}_key", "")
    monkeypatch.setattr(cfg, "monthly_coins", 0)
    gateway.reset(httpx.MockTransport(balance_api.handler))
    router._provider_cache.clear()
    yield balance_api
    gateway.reset()
    router._provider_cache.clear()
    usage.invalidate()


def _ctx():
    return ToolContext(conversation_id=None, phone="77010000000", settings=BotSettings(),
                       now=datetime.now(UTC), dry_run=True)


def gw_error(kind: str) -> LlmError:
    status = 402 if kind == gateway.INSUFFICIENT_COINS else 401
    return LlmError(f"anthropic HTTP {status}: gateway says no", retryable=False, status=status, error_type=kind)


# --- client construction ------------------------------------------------------------------------

async def test_default_factory_points_every_provider_at_the_gateway(gw_mode):
    router.set_provider_factory(None)
    s = LlmSettings()
    a = router._default_factory("anthropic", s)
    o = router._default_factory("openai", s)
    g = router._default_factory("gemini", s)
    assert str(a._client.base_url).rstrip("/") == f"{GW}/llm/anthropic" and a._client.api_key == VK
    assert a._client.default_headers.get("x-api-key") == VK  # the gateway reads Anthropic's native header
    assert str(o._client.base_url).rstrip("/") == f"{GW}/llm/openai/v1" and o._client.api_key == VK
    opts = g._client._api_client._http_options
    assert opts.base_url.rstrip("/") == f"{GW}/llm/gemini" and opts.api_version == "v1beta"
    assert opts.headers["x-goog-api-key"] == VK


async def test_default_factory_off_uses_vendor_keys_and_default_urls(monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "llm_gateway_url", "")
    monkeypatch.setattr(cfg, "anthropic_key", "sk-ant-api03-test")
    monkeypatch.setattr(cfg, "openai_key", "sk-test")
    router._provider_cache.clear()
    router.set_provider_factory(None)
    try:
        a = router._default_factory("anthropic", LlmSettings())
        o = router._default_factory("openai", LlmSettings())
        assert str(a._client.base_url).rstrip("/") == "https://api.anthropic.com"
        assert a._client.api_key == "sk-ant-api03-test"
        assert str(o._client.base_url).rstrip("/") == "https://api.openai.com/v1" and o._client.api_key == "sk-test"
        assert router.provider_base_url("gemini") is None
        monkeypatch.setattr(cfg, "openai_base_url", "http://llm.lan/v1")  # optional per-provider override
        assert router.provider_base_url("openai") == "http://llm.lan/v1"
    finally:
        router._provider_cache.clear()


async def test_requests_go_to_gateway_routes_with_native_key_headers(gw_mode):
    seen: list = []

    def anthropic_handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers.get("authorization") == f"Bearer {VK}"  # what anthropic 2.x sends anyway
        seen.append(("anthropic", str(request.url), request.headers.get("x-api-key")))
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                                          "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                                          "usage": {"input_tokens": 1, "output_tokens": 1}})

    def openai_handler(request: httpx.Request) -> httpx.Response:
        seen.append(("openai", str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json={"id": "c", "object": "chat.completion", "created": 0, "model": "gpt-x",
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": "ok"}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def gemini_handler(request: httpx.Request) -> httpx.Response:
        seen.append(("gemini", str(request.url), request.headers.get("x-goog-api-key")))
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]})

    a = AnthropicProvider(VK, base_url=gateway.base_url("anthropic"), native_key_header=True,
                          http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(anthropic_handler)))
    o = OpenAIProvider(VK, base_url=gateway.base_url("openai"),
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(openai_handler)))
    g = GeminiProvider(VK, base_url=gateway.base_url("gemini"), api_version=gateway.GEMINI_API_VERSION,
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(gemini_handler)))
    for p, model in ((a, "claude-haiku-5-5"), (o, "gpt-x"), (g, "gemini-x")):
        assert (await p.complete(MSGS, [], model)).text == "ok"
    assert seen == [
        ("anthropic", f"{GW}/llm/anthropic/v1/messages", VK),
        ("openai", f"{GW}/llm/openai/v1/chat/completions", f"Bearer {VK}"),
        ("gemini", f"{GW}/llm/gemini/v1beta/models/gemini-x:generateContent", VK),
    ]


async def test_gateway_error_types_are_parsed_and_key_redacted(gw_mode):
    def anthropic_402(request):
        return httpx2.Response(402, json={"error": {"type": "insufficient_coins", "message": f"no coins {VK}"}})

    def openai_401(request):
        return httpx.Response(401, json={"error": {"type": "key_revoked"}})

    def gemini_402(request):
        return httpx.Response(402, json={"error": {"type": "insufficient_coins", "message": "no coins"}})

    a = AnthropicProvider(VK, base_url=gateway.base_url("anthropic"),
                          http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(anthropic_402)))
    o = OpenAIProvider(VK, base_url=gateway.base_url("openai"),
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(openai_401)))
    g = GeminiProvider(VK, base_url=gateway.base_url("gemini"), api_version="v1beta",
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(gemini_402)))
    errors = []
    for p in (a, o, g):
        with pytest.raises(LlmError) as ei:
            await p.complete(MSGS, [], "m")
        errors.append(ei.value)
    assert [(e.status, e.error_type) for e in errors] == [
        (402, "insufficient_coins"), (401, "key_revoked"), (402, "insufficient_coins")]
    assert all(VK not in str(e) for e in errors)
    assert [gateway.blocking_error(e) for e in errors] == ["insufficient_coins", "key_revoked", "insufficient_coins"]


def test_blocking_error_only_in_gateway_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_gateway_url", "")
    assert gateway.blocking_error(gw_error("insufficient_coins")) is None


# --- providers list -----------------------------------------------------------------------------

async def test_providers_configured_in_gateway_mode_without_vendor_keys(client, gw_mode, monkeypatch):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    r = await client.get("/api/settings/llm/providers")
    assert r.json() == {"providers": ["anthropic", "openai", "gemini"]}
    assert VK not in r.text and VK not in (await client.get("/api/settings/llm")).text
    monkeypatch.setattr(get_settings(), "llm_gateway_key", "")  # gateway without a virtual key: nothing works
    assert (await client.get("/api/settings/llm/providers")).json() == {"providers": []}
    monkeypatch.setattr(get_settings(), "llm_gateway_url", "")  # off: as before, only providers with a key
    monkeypatch.setattr(get_settings(), "gemini_key", "AIza-test")
    assert (await client.get("/api/settings/llm/providers")).json() == {"providers": ["gemini"]}


async def test_providers_limited_to_what_the_gateway_serves(client, gw_mode):
    """Cloud with only a company Claude key: the owner is offered Claude only (no GPT / Gemini to fail)."""
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    gw_mode.set(providers=["anthropic"])
    expire_balance_cache()
    assert (await client.get("/api/settings/llm/providers")).json() == {"providers": ["anthropic"]}
    gw_mode.set(providers=["anthropic", "openai"])
    expire_balance_cache()
    assert (await client.get("/api/settings/llm/providers")).json() == {"providers": ["anthropic", "openai"]}
    gw_mode.body.pop("providers")  # an older gateway that does not say: all of them, as before
    expire_balance_cache()
    assert (await client.get("/api/settings/llm/providers")).json() == {"providers": ["anthropic", "openai", "gemini"]}


# --- router: 402 / 401 --------------------------------------------------------------------------

async def test_router_402_blocks_without_fallbacks_until_balance_returns(pool, gw_mode):
    primary = FakeLLM([gw_error("insufficient_coins")], default="ok", name="anthropic")
    emergency = FakeLLM(default="must not be used", name="gemini")
    router.set_provider_factory(lambda name, s: primary if name == "anthropic" else emergency)
    s = LlmSettings(provider="anthropic", emergency_provider="gemini")
    try:
        with pytest.raises(usage.CoinsExhausted) as ei:
            await router.complete_with_tools(MSGS, [], _ctx(), s)
        assert not isinstance(ei.value, usage.AccessSuspended)
        assert emergency.calls == [] and len(primary.calls) == 1
        # later turns: no LLM call while the balance read after the 402 is not back
        with pytest.raises(usage.CoinsExhausted):
            await router.complete_with_tools(MSGS, [], _ctx(), s)
        expire_balance_cache()
        gw_mode.set(coins_left=0)
        with pytest.raises(usage.CoinsExhausted):
            await router.complete_with_tools(MSGS, [], _ctx(), s)
        assert len(primary.calls) == 1
        # topped up: the next read after the cache expires lifts the block
        expire_balance_cache()
        gw_mode.set(coins_left=800)
        assert (await router.complete_with_tools(MSGS, [], _ctx(), s)).text == "ok"
        assert len(primary.calls) == 2
    finally:
        router.set_provider_factory(None)


async def test_router_local_limit_ignored_in_gateway_mode(pool, gw_mode, monkeypatch):
    monkeypatch.setattr(get_settings(), "monthly_coins", 1)
    await pool.execute("INSERT INTO ai_requests(provider, model, input_tokens, output_tokens)"
                       " VALUES ('anthropic', 'claude-sonnet-5-5', 100000, 0)")  # 200 coins locally
    assert not await usage.limit_reached()
    fake = FakeLLM(default="ok")
    router.set_provider_factory(lambda name, s: fake)
    try:
        assert (await router.complete_with_tools(MSGS, [], _ctx(), LlmSettings())).text == "ok"
    finally:
        router.set_provider_factory(None)


async def test_router_402_is_a_plain_llm_error_without_gateway(pool, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_gateway_url", "")
    fake = FakeLLM([gw_error("insufficient_coins")])
    router.set_provider_factory(lambda name, s: fake)
    try:
        with pytest.raises(LlmError) as ei:
            await router.complete_with_tools(MSGS, [], _ctx(), LlmSettings())
        assert not isinstance(ei.value, usage.CoinsExhausted)
    finally:
        router.set_provider_factory(None)


# --- engine: handover -----------------------------------------------------------------------------

async def _conv(pool):
    return await pool.fetchrow("SELECT * FROM conversations WHERE chat_jid = $1", CLIENT_JID)


async def _release(pool, conv_id):
    await pool.execute("UPDATE conversations SET status = 'active', paused_until = NULL WHERE id = $1", conv_id)


async def test_402_hands_over_with_one_holding_reply_and_no_more_llm_calls(env, pool, gw_mode):
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    env.llm = FakeLLM([gw_error("insufficient_coins")], default="Бот снова на связи")
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    conv = await _conv(pool)
    assert conv["status"] == "needs_operator"
    assert env.gw.texts(CLIENT_JID) == [HOLD]
    admin = env.gw.texts(ADMIN_JID)
    assert any("Коины" in t and "ждёт оператора" in t for t in admin)
    assert len(env.llm.calls) == 1
    # the client writes again (the operator released the chat): no LLM call, no second holding reply
    await _release(pool, conv["id"])
    await handler.handle_incoming(make_msg("Алло?"))
    await env.idle()
    assert len(env.llm.calls) == 1
    assert env.gw.texts(CLIENT_JID) == [HOLD]
    assert (await _conv(pool))["status"] == "needs_operator"
    # balance is back: the bot answers again
    await _release(pool, conv["id"])
    expire_balance_cache()
    await handler.handle_incoming(make_msg("Есть кто?"))
    await env.idle()
    assert len(env.llm.calls) == 2
    assert env.gw.texts(CLIENT_JID) == [HOLD, "Бот снова на связи"]


@pytest.mark.parametrize(("brand", "company"), [("iler", "ILER"), ("nox", "Nox service")])
async def test_key_revoked_hands_over_and_alerts_admins(env, pool, gw_mode, monkeypatch, brand, company):
    monkeypatch.setattr(get_settings(), "brand", brand)
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    env.llm = FakeLLM([gw_error("key_revoked")], default="must not be used")
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    assert (await _conv(pool))["status"] == "needs_operator"
    assert env.gw.texts(CLIENT_JID) == [HOLD]
    assert any(f"Доступ к моделям приостановлен — обратитесь в {company}." in t for t in env.gw.texts(ADMIN_JID))
    assert gateway.current_block() == gateway.KEY_REVOKED
    await _release(pool, (await _conv(pool))["id"])
    await handler.handle_incoming(make_msg("Алло?"))
    await env.idle()
    assert len(env.llm.calls) == 1 and env.gw.texts(CLIENT_JID) == [HOLD]


async def test_test_chat_reports_gateway_refusals(client, gw_mode):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200
    await settings_store.put("bot", BotSettings(prompt="Ты бот."))
    headers = {"X-ILER-CSRF": client.cookies.get("iler_csrf", "")}
    body = {"messages": [{"role": "user", "content": "hi"}]}
    router.set_provider_factory(lambda name, s: FakeLLM([gw_error("insufficient_coins")]))
    try:
        r = await client.post("/api/test-chat", json=body, headers=headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "coins_exhausted"
        gateway.reset(httpx.MockTransport(gw_mode.handler))
        router.set_provider_factory(lambda name, s: FakeLLM([gw_error("key_revoked")]))
        r = await client.post("/api/test-chat", json=body, headers=headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "llm_access_suspended"
        assert "Доступ к моделям приостановлен" in r.json()["error"]["message"]
    finally:
        router.set_provider_factory(None)


# --- balance cache ------------------------------------------------------------------------------

async def test_balance_cached_for_a_minute_and_sent_with_the_virtual_key(gw_mode):
    v1 = await gateway.balance()
    v2 = await gateway.balance()
    assert len(gw_mode.requests) == 1
    req = gw_mode.requests[0]
    assert str(req.url) == f"{GW}/llm/v1/balance"
    assert req.headers["authorization"] == f"Bearer {VK}" and req.headers["x-api-key"] == VK
    assert v1.balance.coins_left == 5000 and v1.balance.monthly_limit == 10000 and not v1.stale and v2 == v1
    expire_balance_cache()
    gw_mode.set(coins_left=4000)
    assert (await gateway.balance()).balance.coins_left == 4000 and len(gw_mode.requests) == 2


async def test_balance_errors_keep_last_value_and_mark_stale(gw_mode, caplog):
    await gateway.balance()
    gw_mode.status = 503
    expire_balance_cache()
    view = await gateway.balance()
    assert view.stale and view.balance.coins_left == 5000 and view.fetched_at is not None
    assert await gateway.blocked() is None  # an unreadable balance never blocks by itself
    gw_mode.status = 200
    gw_mode.body = {"unexpected": True}
    expire_balance_cache()
    assert (await gateway.balance()).stale
    assert VK not in caplog.text


async def test_zero_or_revoked_balance_blocks_before_any_call(gw_mode):
    gw_mode.set(coins_left=0)
    assert await gateway.blocked() == gateway.INSUFFICIENT_COINS
    expire_balance_cache()
    gw_mode.set(coins_left=10, status="suspended")
    assert await gateway.blocked() == gateway.KEY_REVOKED


async def test_dashboard_shows_gateway_balance_and_tolerates_errors(client, gw_mode, monkeypatch):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200
    monkeypatch.setattr(get_settings(), "topup_url", "https://pay.example.kz/topup")
    r = await client.get("/api/dashboard")
    assert r.status_code == 200 and VK not in r.text
    coins = r.json()["coins"]
    assert coins["source"] == "gateway" and coins["limit"] == 10000 and coins["remaining"] == 5000
    assert coins["spent"] == 5000 and coins["percent"] == 50.0 and coins["exhausted"] is False
    assert coins["period_end"] == "2026-12-01T00:00:00+05:00" and coins["stale"] is False
    assert coins["topup_url"] == "https://pay.example.kz/topup" and coins["blocked"] is None
    # the gateway goes down: last known value, stale flag, no crash
    gw_mode.status = 500
    expire_balance_cache()
    coins = (await client.get("/api/dashboard")).json()["coins"]
    assert coins["remaining"] == 5000 and coins["stale"] is True
    # never read at all
    gateway.reset(httpx.MockTransport(gw_mode.handler))
    coins = (await client.get("/api/dashboard")).json()["coins"]
    assert coins["source"] == "gateway" and coins["limit"] == 0 and coins["remaining"] is None and coins["stale"]
    # a 402 after the last read: shown as exhausted
    gw_mode.status = 200
    gateway.reset(httpx.MockTransport(gw_mode.handler))
    await gateway.balance()
    gateway.mark_blocked(gateway.INSUFFICIENT_COINS)
    coins = (await client.get("/api/dashboard")).json()["coins"]
    assert coins["exhausted"] is True and coins["remaining"] == 0 and coins["blocked"] == "insufficient_coins"


async def test_dashboard_local_mode_has_no_gateway_fields(client, monkeypatch):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200
    monkeypatch.setattr(get_settings(), "topup_url", "javascript:alert(1)")  # only http(s) links pass
    coins = (await client.get("/api/dashboard")).json()["coins"]
    assert coins["source"] == "local" and coins["topup_url"] is None and coins["stale"] is False


# --- alerts ------------------------------------------------------------------------------------

@pytest.fixture
def notified(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_notify(text, *, key=None):
        sent.append((key, text))
        return []

    monkeypatch.setattr(sender, "notify_admins", fake_notify)
    return sent


async def test_gateway_alerts_at_20_5_and_0_percent_left(pool, gw_mode, notified, monkeypatch):
    monkeypatch.setattr(get_settings(), "topup_url", "https://pay.example.kz/topup")
    period = gw_mode.body["period_end"]
    gw_mode.set(coins_left=2500)  # 75% spent: nothing
    await usage.after_spend()
    assert notified == []
    gw_mode.set(coins_left=1900)  # 81% spent: 20% left
    expire_balance_cache()
    await usage.after_spend()
    await usage.after_spend()
    assert [k for k, _ in notified] == [f"coins-gw:{period}:10000:80"] and "81%" in notified[0][1]
    gw_mode.set(coins_left=400)  # 96%: 5% left
    expire_balance_cache()
    await usage.after_spend()
    assert notified[-1][0] == f"coins-gw:{period}:10000:95" and "осталось 400" in notified[-1][1]
    gateway.mark_blocked(gateway.INSUFFICIENT_COINS)  # the 402 came before the cache saw zero
    await usage.after_spend()
    assert notified[-1][0] == f"coins-gw:{period}:10000:100"
    assert "коины закончились" in notified[-1][1] and "https://pay.example.kz/topup" in notified[-1][1]
    assert len(notified) == 3
    # a new period re-arms the levels
    expire_balance_cache()
    gw_mode.set(coins_left=10000, period_end="2027-01-01T00:00:00+05:00")
    await usage.after_spend()
    assert len(notified) == 3 and gateway.current_block() is None


async def test_local_alert_at_5_percent_left(pool, notified, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_gateway_url", "")
    monkeypatch.setattr(get_settings(), "monthly_coins", 100)
    await pool.execute("INSERT INTO ai_requests(provider, model, input_tokens, output_tokens)"
                       " VALUES ('anthropic', 'claude-sonnet-5-5', 48000, 0)")  # 96 coins
    await usage.after_spend()
    month = f"{usage.month_start(datetime.now(UTC), usage._tz()):%Y-%m}"
    assert [k for k, _ in notified] == [f"coins:{month}:100:95"] and "96%" in notified[0][1]
    state = await settings_store.get("usage_alerts", usage.UsageAlerts)
    assert state.sent == [80, 95]
    usage.invalidate()


def test_no_coins_message_default_and_override():
    from iler.engine.turn import no_coins_message

    assert no_coins_message(BotSettings()) == HOLD
    assert no_coins_message(BotSettings(no_coins_message="  ")) == HOLD
    assert no_coins_message(BotSettings(no_coins_message="Ждите менеджера")) == "Ждите менеджера"
    assert json.loads(BotSettings().model_dump_json())["no_coins_message"] == HOLD
