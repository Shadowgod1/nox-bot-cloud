"""LLM gateway proxy: auth, refusals, passthrough (plain and streamed) for the three providers, metering."""

import asyncio
import json
import logging
from decimal import Decimal

import asyncpg
import httpx
import pytest

from nox_portal import mailer
from nox_portal.config import get_settings
from tests.gw import (
    ANTHROPIC_MSG, ANTHROPIC_MSG_COINS, ANTHROPIC_SSE, ANTHROPIC_SSE_COINS, COMPANY, GEMINI_JSON, GEMINI_SSE,
    OPENAI_CHAT, OPENAI_RESPONSES_SSE, make_account, openai_chunks, settled, sse, upstream,  # noqa: F401
)

MSG_BODY = {"model": "claude-sonnet-5-5", "max_tokens": 1024, "messages": [{"role": "user", "content": "Привет"}]}


async def _usage(pool):
    return await pool.fetch("SELECT * FROM llm_usage ORDER BY id")


def _no_vk_upstream(up, key: str) -> None:
    for req, body in zip(up.requests, up.bodies):
        assert key not in str(req.url)
        assert all(key not in v for v in req.headers.values())
        assert key.encode() not in body


# ---- auth -----------------------------------------------------------------------------------


@pytest.mark.parametrize("style", ["x-api-key", "bearer", "both"])
async def test_anthropic_auth_styles(client, pool, upstream, style):
    """Core sends both Authorization: Bearer and x-api-key; either alone works too."""
    _, key = await make_account(pool)
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    headers = {"anthropic-version": "2023-06-01", "Cookie": "nox_session=x"}
    if style in ("x-api-key", "both"):
        headers["x-api-key"] = key
    if style in ("bearer", "both"):
        headers["Authorization"] = f"Bearer {key}"
    r = await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == ANTHROPIC_MSG
    sent = upstream.requests[0]
    assert sent.headers["x-api-key"] == COMPANY["anthropic"]
    assert "authorization" not in sent.headers and "cookie" not in sent.headers
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert json.loads(upstream.bodies[0]) == MSG_BODY
    _no_vk_upstream(upstream, key)


async def test_openai_bearer_and_gemini_goog_key(client, pool, upstream):
    _, key = await make_account(pool)
    upstream.on("POST", "openai.test/v1/chat/completions", lambda r: OPENAI_CHAT)
    upstream.on("POST", "gemini.test/v1beta/models/gemini-3.8-flash:generateContent", lambda r: GEMINI_JSON)
    r = await client.post("/llm/openai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json={"model": "gpt-5.4-mini", "messages": []})
    assert r.status_code == 200 and r.json() == OPENAI_CHAT
    assert upstream.requests[0].headers["authorization"] == f"Bearer {COMPANY['openai']}"
    r = await client.post("/llm/gemini/v1beta/models/gemini-3.8-flash:generateContent",
                          headers={"x-goog-api-key": key}, json={"contents": []})
    assert r.status_code == 200 and r.json() == GEMINI_JSON
    assert upstream.requests[1].headers["x-goog-api-key"] == COMPANY["gemini"]
    assert "authorization" not in upstream.requests[1].headers
    _no_vk_upstream(upstream, key)


async def test_key_in_url_is_refused(client, pool, upstream):
    _, key = await make_account(pool)
    r = await client.post(f"/llm/gemini/v1beta/models/gemini-3.8-flash:generateContent?key={key}", json={})
    assert r.status_code == 400 and r.json()["error"]["type"] == "key_in_url"
    r = await client.get(f"/llm/v1/balance?key={key}")
    assert r.status_code == 400
    assert upstream.requests == []


@pytest.mark.parametrize("headers", [{}, {"x-api-key": "nox_vk_" + "x" * 43}, {"Authorization": "Bearer sk-ant-123"},
                                     {"x-goog-api-key": "garbage"}])
async def test_unknown_key_401(client, pool, upstream, headers):
    await make_account(pool)
    r = await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers=headers)
    assert r.status_code == 401 and r.json()["error"]["type"] == "invalid_key"
    assert upstream.requests == []


async def test_revoked_key_and_suspended_account_401(client, pool, upstream):
    aid, key = await make_account(pool)
    await pool.execute("UPDATE accounts SET status = 'suspended' WHERE id = $1", aid)
    r = await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})
    assert r.status_code == 401 and r.json()["error"]["type"] == "key_revoked"
    await pool.execute("UPDATE accounts SET status = 'active' WHERE id = $1", aid)
    await pool.execute("UPDATE virtual_keys SET status = 'revoked'")
    r = await client.post("/llm/openai/v1/chat/completions", json={}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401
    assert r.json() == {"error": {"type": "key_revoked", "message": "Ключ отозван"}}
    assert upstream.requests == []


async def test_zero_balance_402_before_upstream(client, pool, upstream):
    await make_account(pool, coins=0)
    _, key = await make_account(pool, coins=0, email="b@example.kz", slug="bbb")
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    r = await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})
    assert r.status_code == 402
    assert r.json() == {"error": {"type": "insufficient_coins", "message": "Коины закончились"}}
    assert upstream.requests == []
    # unmetered endpoints (model list) still work with a zero balance
    upstream.on("GET", "anthropic.test/v1/models", lambda r: {"data": [{"id": "claude-haiku-5-5"}]})
    assert (await client.get("/llm/anthropic/v1/models", headers={"x-api-key": key})).status_code == 200


async def test_endpoint_allowlist_and_unknown_provider(client, pool, upstream):
    _, key = await make_account(pool)
    for path in ("/llm/openai/v1/files", "/llm/openai/v1/batches", "/llm/anthropic/v1/messages/batches",
                 "/llm/gemini/v1beta/files", "/llm/openai/v1/../v1/files"):
        r = await client.post(path, json={}, headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 404, path
    r = await client.post("/llm/mistral/v1/chat", json={}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 404 and r.json()["error"]["type"] == "endpoint_not_allowed"
    assert upstream.requests == []


async def test_provider_without_key_503(client, pool, upstream, monkeypatch):
    _, key = await make_account(pool)
    monkeypatch.setattr(get_settings(), "upstream_gemini_key", "")
    r = await client.post("/llm/gemini/v1beta/models/gemini-3.8-flash:generateContent", json={},
                          headers={"x-goog-api-key": key})
    assert r.status_code == 503 and r.json()["error"]["type"] == "provider_not_configured"


async def test_body_cap_and_rate_limit(client, pool, upstream, monkeypatch):
    _, key = await make_account(pool, coins=100000)
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    monkeypatch.setattr(get_settings(), "gateway_max_body_bytes", 2000)
    r = await client.post("/llm/anthropic/v1/messages", content=b"x" * 3000, headers={"x-api-key": key})
    assert r.status_code == 413 and r.json()["error"]["type"] == "request_too_large"
    from nox_portal import gateway

    gateway.reset_limits()
    monkeypatch.setattr(get_settings(), "gateway_rate_per_minute", 3)
    codes = [(await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})).status_code
             for _ in range(3)]
    assert codes == [200, 200, 200]
    r = await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})
    assert r.status_code == 429 and r.json()["error"]["type"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1


# ---- passthrough and metering ---------------------------------------------------------------


async def test_anthropic_non_stream_metered_with_cache_tokens(client, pool, upstream):
    aid, key = await make_account(pool)
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    r = await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})
    assert r.status_code == 200
    assert r.headers["request-id"] == "req_up_1" and r.headers["x-nox-request-id"].startswith("gw_")
    await settled()
    [row] = await _usage(pool)
    assert (row["provider"], row["model"], row["endpoint"]) == ("anthropic", "claude-sonnet-5-5", "v1/messages")
    assert (row["input_tokens"], row["output_tokens"], row["cache_write_tokens"], row["cache_read_tokens"]) == (
        1000, 500, 2000, 4000)
    assert str(row["coins"]) == ANTHROPIC_MSG_COINS and row["priced"] is True
    assert row["upstream_request_id"] == "msg_01" and row["request_id"] == r.headers["x-nox-request-id"]
    assert await pool.fetchval("SELECT count(*) FROM llm_reservations") == 0
    bal = (await client.get("/llm/v1/balance", headers={"x-api-key": key})).json()
    assert bal["coins_left"] == 1000 - 13  # 987.2 -> 987


async def test_anthropic_stream_passthrough_split_events(client, pool, upstream):
    _, key = await make_account(pool)
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: sse(ANTHROPIC_SSE, pieces=7))
    r = await client.post("/llm/anthropic/v1/messages", json={**MSG_BODY, "stream": True}, headers={"x-api-key": key})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-accel-buffering"] == "no"
    assert r.content == ANTHROPIC_SSE  # byte-for-byte
    await settled()
    [row] = await _usage(pool)
    assert (row["model"], row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]) == (
        "claude-haiku-5-5", 300, 200, 100)
    assert str(row["coins"]) == ANTHROPIC_SSE_COINS


async def test_openai_stream_injects_and_hides_usage_chunk(client, pool, upstream):
    _, key = await make_account(pool)
    upstream.on("POST", "openai.test/v1/chat/completions", lambda r: sse(openai_chunks(include_usage=True), pieces=3))
    body = {"model": "gpt-5.4-mini", "stream": True, "messages": [{"role": "user", "content": "x"}]}
    r = await client.post("/llm/openai/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    sent = json.loads(upstream.bodies[0])
    assert sent["stream_options"] == {"include_usage": True}
    events = [e for e in r.text.split("\n\n") if e]
    assert events[-1] == "data: [DONE]"
    assert all('"choices": []' not in e for e in events), "the injected usage-only chunk is not forwarded"
    assert len(events) == 3
    await settled()
    [row] = await _usage(pool)
    assert (row["input_tokens"], row["output_tokens"]) == (40, 7)


async def test_openai_stream_with_client_usage_is_untouched(client, pool, upstream):
    _, key = await make_account(pool)
    raw = b"".join(openai_chunks(include_usage=True))
    upstream.on("POST", "openai.test/v1/chat/completions", lambda r: sse(raw, pieces=5))
    body = {"model": "gpt-5.4-mini", "stream": True, "stream_options": {"include_usage": True}, "messages": []}
    r = await client.post("/llm/openai/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
    assert r.content == raw
    assert json.loads(upstream.bodies[0]) == body


async def test_openai_responses_stream_and_unknown_model_price(client, pool, upstream, caplog):
    _, key = await make_account(pool)
    upstream.on("POST", "openai.test/v1/responses", lambda r: sse(OPENAI_RESPONSES_SSE))
    with caplog.at_level(logging.WARNING, logger="nox_portal.prices"):
        r = await client.post("/llm/openai/v1/responses", json={"model": "gpt-5.4-mini", "stream": True, "input": "x"},
                              headers={"Authorization": f"Bearer {key}"})
        assert r.content == OPENAI_RESPONSES_SSE
        await settled()
    [row] = await _usage(pool)
    assert (row["input_tokens"], row["output_tokens"]) == (120, 30)
    # no OpenAI price in the table (like the core): fallback 2/10 $/Mtok, never 0, flagged
    assert row["priced"] is False and row["coins"] == Decimal("0.540")


async def test_gemini_json_and_sse_stream(client, pool, upstream):
    _, key = await make_account(pool)
    upstream.on("POST", "gemini.test/v1beta/models/gemini-3.8-flash:generateContent", lambda r: GEMINI_JSON)
    upstream.on("POST", "gemini.test/v1beta/models/gemini-3.8-flash:streamGenerateContent",
                lambda r: sse(GEMINI_SSE, pieces=4))
    h = {"x-goog-api-key": key}
    assert (await client.post("/llm/gemini/v1beta/models/gemini-3.8-flash:generateContent", json={}, headers=h)).json() \
        == GEMINI_JSON
    r = await client.post("/llm/gemini/v1beta/models/gemini-3.8-flash:streamGenerateContent?alt=sse", json={},
                          headers=h)
    assert r.content == GEMINI_SSE
    assert upstream.requests[1].url.params["alt"] == "sse"
    await settled()
    rows = await _usage(pool)
    assert [(x["input_tokens"], x["output_tokens"]) for x in rows] == [(100, 75), (10, 25)]
    assert rows[0]["model"] == "gemini-3.8-flash" and rows[0]["upstream_request_id"] == "gem-1"


async def test_upstream_error_passed_through_not_charged(client, pool, upstream):
    _, key = await make_account(pool)
    err = {"type": "error", "error": {"type": "invalid_request_error", "message": "max_tokens: required"}}
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: httpx.Response(400, json=err))
    r = await client.post("/llm/anthropic/v1/messages", json={"model": "claude-sonnet-5-5"}, headers={"x-api-key": key})
    assert r.status_code == 400 and r.json() == err
    await settled()
    assert await _usage(pool) == []
    assert await pool.fetchval("SELECT count(*) FROM llm_reservations") == 0


async def test_success_without_usage_is_charged_estimate(client, pool, upstream):
    _, key = await make_account(pool)
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: {"type": "message", "model": "claude-sonnet-5-5"})
    await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})
    await settled()
    [row] = await _usage(pool)
    assert row["priced"] is False and row["coins"] > 0


async def test_upstream_timeout_504_releases_reservation(client, pool, upstream):
    _, key = await make_account(pool)

    def boom(_):
        raise httpx.ReadTimeout("slow")

    upstream.on("POST", "anthropic.test/v1/messages", boom)
    r = await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})
    assert r.status_code == 504 and r.json()["error"]["type"] == "upstream_unavailable"
    assert await pool.fetchval("SELECT count(*) FROM llm_reservations") == 0


async def test_ledger_is_append_only(client, pool, upstream):
    _, key = await make_account(pool)
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})
    await settled()
    for sql in ("UPDATE llm_usage SET coins = 0", "DELETE FROM llm_usage", "TRUNCATE llm_usage",
                "UPDATE coin_grants SET coins = 1", "DELETE FROM coin_grants", "TRUNCATE coin_grants"):
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await pool.execute(sql)


async def test_concurrent_requests_overspend_at_most_one(client, pool, upstream):
    """Balance 5 coins, each request costs 3.6 coins: parallel requests may not overspend by more than one."""
    aid, key = await make_account(pool, coins=5)
    answer = {**ANTHROPIC_MSG, "usage": {"input_tokens": 300, "output_tokens": 300}}  # 600 + 3000 = 3.6 coins

    async def slow(_):
        await asyncio.sleep(0.2)
        return answer

    upstream.on("POST", "anthropic.test/v1/messages", slow)
    body = {**MSG_BODY, "max_tokens": 300}
    results = await asyncio.gather(*[client.post("/llm/anthropic/v1/messages", json=body, headers={"x-api-key": key})
                                     for _ in range(6)])
    await settled()
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) >= 1 and set(codes) <= {200, 402}
    spent = await pool.fetchval("SELECT sum(coins) FROM llm_usage WHERE account_id = $1", aid)
    assert spent <= Decimal(5) + Decimal("3.6")
    assert codes.count(200) <= 2
    r = await client.post("/llm/anthropic/v1/messages", json=body, headers={"x-api-key": key})
    assert r.status_code == 402


async def test_balance_endpoint(client, pool, upstream):
    aid, key = await make_account(pool, coins=30000)
    for headers in ({"x-api-key": key}, {"Authorization": f"Bearer {key}"}, {"x-goog-api-key": key},
                    {"x-api-key": key, "Authorization": f"Bearer {key}"}):
        r = await client.get("/llm/v1/balance", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["coins_left"] == 30000 and body["monthly_limit"] == 30000 and body["status"] == "active"
        assert body["period_end"].startswith("20")
    await pool.execute("UPDATE accounts SET status = 'suspended'")
    assert (await client.get("/llm/v1/balance", headers={"x-api-key": key})).json()["status"] == "suspended"
    await pool.execute("UPDATE virtual_keys SET status = 'revoked'")
    assert (await client.get("/llm/v1/balance", headers={"x-api-key": key})).json()["status"] == "revoked"
    assert (await client.get("/llm/v1/balance")).status_code == 401


async def test_alerts_on_company_key_rejected(client, pool, upstream, monkeypatch, caplog):
    from nox_portal import alerts

    _, key = await make_account(pool)
    token = "123456:telegram-test-token"
    monkeypatch.setattr(get_settings(), "alert_telegram_bot_token", token)
    monkeypatch.setattr(get_settings(), "alert_telegram_chat_id", "-100500")
    tg = []
    upstream.on("POST", f"telegram.test/bot{token}/sendMessage", lambda r: tg.append(json.loads(r.content)) or {"ok": True})
    upstream.on("POST", "anthropic.test/v1/messages",
                lambda r: httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error"}}))
    mailer.OUTBOX.clear()
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            r = await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})
            assert r.status_code == 401  # upstream status passed through
        await settled()
        await alerts.drain()
    assert len(tg) == 1, "cool-down: one alert"
    assert tg[0]["chat_id"] == "-100500" and "anthropic" in tg[0]["text"]
    assert [m.to for m in mailer.OUTBOX] == ["staff@example.kz"]
    assert token not in caplog.text


async def test_alerts_on_credit_and_5xx_threshold(client, pool, upstream, monkeypatch):
    from nox_portal import alerts

    _, key = await make_account(pool, coins=100000)
    monkeypatch.setattr(get_settings(), "alert_error_threshold", 3)
    upstream.on("POST", "openai.test/v1/chat/completions",
                lambda r: httpx.Response(429, json={"error": {"type": "insufficient_quota",
                                                              "message": "You exceeded your current quota"}}))
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: httpx.Response(529, json={"type": "error"}))
    mailer.OUTBOX.clear()
    await client.post("/llm/openai/v1/chat/completions", json={"model": "gpt-5.4-mini"},
                      headers={"Authorization": f"Bearer {key}"})
    await settled()
    await alerts.drain()
    assert len(mailer.OUTBOX) == 1 and "credit" in mailer.OUTBOX[0].subject
    for i in range(3):
        await client.post("/llm/anthropic/v1/messages", json=MSG_BODY, headers={"x-api-key": key})
        await settled()
        await alerts.drain()
        assert len(mailer.OUTBOX) == (1 if i < 2 else 2)
    assert "errors" in mailer.OUTBOX[1].subject
