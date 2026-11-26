"""Helpers for the LLM gateway tests: a programmable mock upstream (httpx.MockTransport, no network),
canned provider answers and accounts with a virtual key."""

import asyncio
import json

import httpx
import pytest

from nox_portal import alerts, gateway, wallet
from nox_portal.security import hash_password_sync

COMPANY = {"anthropic": "sk-ant-api03-company-test-key", "openai": "sk-proj-company-test-key",
           "gemini": "gemini-company-test-key"}
PASSWORD = "correct-horse-1"

ANTHROPIC_MSG = {
    "id": "msg_01", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
    "content": [{"type": "text", "text": "Здравствуйте!"}], "stop_reason": "end_turn",
    "usage": {"input_tokens": 1000, "output_tokens": 500, "cache_creation_input_tokens": 2000,
              "cache_read_input_tokens": 4000},
}
# sonnet 2/10 $/Mtok, cache write 2.5, cache read 0.10 (0.05x): 2000 + 5000 + 5000 + 400 = 12400 micro-$ = 12.4 coins
ANTHROPIC_MSG_COINS = "12.400"

ANTHROPIC_SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_02","type":"message",'
    b'"model":"claude-haiku-5-5","usage":{"input_tokens":300,"output_tokens":1,"cache_creation_input_tokens":0,'
    b'"cache_read_input_tokens":100}}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82"}}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":200}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)
# haiku 0.1/0.5, cache read 0.01: 30 + 100 + 1 = 131 micro-$ = 0.131 coins
ANTHROPIC_SSE_COINS = "0.131"

OPENAI_CHAT = {"id": "chatcmpl-1", "object": "chat.completion", "model": "gpt-5.4-mini",
               "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}}


def openai_chunks(include_usage: bool) -> list[bytes]:
    base = {"id": "chatcmpl-2", "object": "chat.completion.chunk", "model": "gpt-5.4-mini"}
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Да"}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    if include_usage:
        chunks = [{**c, "usage": None} for c in chunks]
        chunks.append({**base, "choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 7, "total_tokens": 47}})
    out = [f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode() for c in chunks]
    return out + [b"data: [DONE]\n\n"]


OPENAI_RESPONSES_SSE = (
    b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","model":"gpt-5.4-mini",'
    b'"usage":null}}\n\n'
    b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"hi"}\n\n'
    b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-5.4-mini",'
    b'"usage":{"input_tokens":120,"output_tokens":30,"total_tokens":150}}}\n\n'
)

GEMINI_JSON = {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
               "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "thoughtsTokenCount": 25,
                                 "totalTokenCount": 175},
               "modelVersion": "gemini-3.8-flash", "responseId": "gem-1"}
GEMINI_SSE = (
    b'data: {"candidates":[{"content":{"parts":[{"text":"o"}]}}],"usageMetadata":{"promptTokenCount":10,'
    b'"candidatesTokenCount":1},"modelVersion":"gemini-3.8-flash"}\r\n\r\n'
    b'data: {"candidates":[{"content":{"parts":[{"text":"k"}]}}],"usageMetadata":{"promptTokenCount":10,'
    b'"candidatesTokenCount":20,"thoughtsTokenCount":5},"modelVersion":"gemini-3.8-flash"}\r\n\r\n'
)


def sse(body: bytes | list[bytes], pieces: int = 1) -> httpx.Response:
    """SSE response; `pieces` > 1 cuts the stream at arbitrary byte offsets (events split across chunks)."""
    data = body if isinstance(body, bytes) else b"".join(body)

    async def gen():
        step = max(len(data) // pieces, 1)
        for i in range(0, len(data), step):
            yield data[i:i + step]
            await asyncio.sleep(0)

    return httpx.Response(200, headers={"content-type": "text/event-stream", "request-id": "req_up_1"},
                          content=gen())


class Upstream:
    """Mock provider APIs: routes "METHOD host/path" to handlers (sync or async); records every request."""

    def __init__(self) -> None:
        self.routes: dict[str, object] = {}
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []

    def on(self, method: str, url: str, handler) -> None:
        self.routes[f"{method} {url}"] = handler

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        self.requests.append(request)
        self.bodies.append(body)
        key = f"{request.method} {request.url.host}{request.url.path}"
        handler = self.routes.get(key)
        if handler is None:
            return httpx.Response(599, json={"error": f"no mock for {key}"})
        result = handler(request)
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, dict):
            return httpx.Response(200, json=result, headers={"request-id": "req_up_1"})
        return result


@pytest.fixture
async def upstream(app):
    up = Upstream()
    old = gateway.client
    gateway.client = gateway.make_client(httpx.MockTransport(up))
    alerts.client = gateway.client
    yield up
    await gateway.client.aclose()
    gateway.client = alerts.client = old


async def make_account(pool, *, coins: int = 1000, status: str = "active", local: bool = True,
                       email: str = "owner@example.kz", slug: str = "primer", tariff: str = "start") -> tuple[int, str]:
    async with pool.acquire() as conn, conn.transaction():
        aid = await conn.fetchval(
            "INSERT INTO accounts(email, password_hash, company, contact_name, phone, slug, status, consent_at,"
            " email_verified_at, local_install, tariff, coins_limit)"
            " VALUES ($1, $2, 'ТОО Пример', 'Айгерим', '+7 701 000 00 00', $3, $4, now(), now(), $5, $6, $7)"
            " RETURNING id",
            email, hash_password_sync(PASSWORD), slug, status, local, tariff, coins,
        )
        await wallet.start_period(conn, aid, coins, staff_id=None, note="test")
        key, _ = await wallet.issue_key(conn, aid, None)
    return aid, key


async def login(c, email: str = "owner@example.kz") -> None:
    r = await c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text


async def settled() -> None:
    await gateway.drain()
