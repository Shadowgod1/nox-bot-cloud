"""Anthropic (Claude) provider: request mapping, tool loop echo, refusals, errors, router integration.

No network: the SDK talks to an httpx2.MockTransport (anthropic>=1 is built on httpx2, not httpx).
"""

import json
from datetime import datetime, timezone

import httpx2
import pytest
from pydantic import SecretStr

from iler.llm import router
from iler.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from iler.llm.base import ChatMessage, LlmError, LlmResponse
from iler.llm.fake import FakeLLM
from iler.settings_models import BotSettings, LlmSettings
from iler.tools.base import ToolContext, ToolResult

KEY = "sk-ant-test-SECRET-1234"
MSGS = [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="hi")]


class EchoTool:
    name = "echo"
    description = "Echo back the text"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def __init__(self):
        self.calls = []

    def enabled(self, settings):
        return True

    async def run(self, ctx, args):
        self.calls.append(args)
        return ToolResult(content={"echo": args.get("text")})


def _ctx():
    return ToolContext(conversation_id=None, phone="77010000000", settings=BotSettings(),
                       now=datetime.now(timezone.utc), dry_run=True)


def _message(content, stop_reason="end_turn", model="claude-opus-5-5", **extra):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": content,
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 3, "cache_read_input_tokens": 4}, **extra,
    }


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "echo", "input": {"text": "x"}}


class Api:
    """Scripted Messages API: records request bodies/headers, replays (status, json) responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies: list[dict] = []
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.bodies.append(json.loads(request.content))
        status, body = self.responses.pop(0)
        return httpx2.Response(status, json=body)

    def provider(self, key=KEY, **kw) -> AnthropicProvider:
        p = AnthropicProvider(key, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler)), **kw)
        p._client = p._client.with_options(max_retries=0)  # no backoff sleeps in tests
        return p


def _err(status, etype, message):
    return status, {"type": "error", "error": {"type": etype, "message": message}}


@pytest.fixture
def factory():
    providers: dict = {}

    def install(**by_name):
        providers.update(by_name)
        router.set_provider_factory(lambda name, settings: providers[name])

    yield install
    router.set_provider_factory(None)


async def test_request_mapping_system_tools_effort():
    api = Api((200, _message([{"type": "text", "text": "Здравствуйте"}])))
    p = api.provider(effort="medium")
    resp = await p.complete(MSGS, [EchoTool()], "claude-opus-5-5")

    body = api.bodies[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["system"] == "sys"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["tools"] == [{"name": "echo", "description": "Echo back the text",
                              "input_schema": EchoTool.parameters}]
    assert body["tool_choice"] == {"type": "auto"}
    assert body["output_config"] == {"effort": "medium"}
    assert 0 < body["max_tokens"] <= 8192
    for forbidden in ("thinking", "temperature", "top_p", "top_k"):
        assert forbidden not in body
    # server-side refusal fallbacks on the beta endpoint
    assert body["fallbacks"] == "default"
    req = api.requests[0]
    assert FALLBACK_BETA in req.headers.get("anthropic-beta", "")
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert KEY in req.headers.get("authorization", "")  # anthropic 2.x sends the API key as a Bearer token
    assert resp.text == "Здравствуйте" and resp.tool_calls == []
    assert resp.provider == "anthropic" and resp.model == "claude-opus-5-5"
    assert resp.input_tokens == 15 and resp.output_tokens == 3


async def test_no_tools_omits_tool_params_and_haiku_skips_fallbacks():
    api = Api((200, _message([{"type": "text", "text": "ok"}], model="claude-haiku-5-5")))
    await api.provider().complete(MSGS, [], "claude-haiku-5-5")
    body = api.bodies[0]
    assert "tools" not in body and "tool_choice" not in body
    assert "fallbacks" not in body
    assert "anthropic-beta" not in api.requests[0].headers
    assert body["output_config"] == {"effort": "low"}


async def test_tool_use_parsing_and_tool_loop_echoes_raw_content(pool):
    api = Api(
        (200, _message([THINKING, {"type": "text", "text": "Сейчас проверю"}, TOOL_USE], stop_reason="tool_use")),
        (200, _message([{"type": "text", "text": "готово"}])),
    )
    p = api.provider()
    tool = EchoTool()
    router.set_provider_factory(lambda name, settings: p)
    try:
        res = await router.complete_with_tools(MSGS, [tool], _ctx(), LlmSettings(provider="anthropic"))
    finally:
        router.set_provider_factory(None)

    assert res.text == "готово"
    assert tool.calls == [{"text": "x"}]
    first = res.requests[0]
    assert first.tool_calls[0].id == "toolu_1" and first.tool_calls[0].name == "echo"
    assert first.tool_calls[0].arguments == {"text": "x"}

    second = api.bodies[1]["messages"]
    assert second[0] == {"role": "user", "content": "hi"}
    # assistant turn sent back verbatim (thinking block with signature first)
    assert second[1]["role"] == "assistant"
    assert second[1]["content"][0] == THINKING
    assert second[1]["content"][2] == TOOL_USE
    assert second[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": json.dumps({"echo": "x"})}]}


async def test_uncached_assistant_tool_calls_rebuilt_and_parallel_results_merged():
    from iler.llm.base import ToolCall

    api = Api((200, _message([{"type": "text", "text": "ok"}])))
    history = MSGS + [
        ChatMessage(role="assistant", content="", tool_calls=[
            ToolCall(id="t1", name="echo", arguments={"text": "a"}),
            ToolCall(id="t2", name="echo", arguments={"text": "b"})]),
        ChatMessage(role="tool", content='{"echo": "a"}', tool_call_id="t1"),
        ChatMessage(role="tool", content="error: boom", tool_call_id="t2"),
    ]
    await api.provider().complete(history, [EchoTool()], "claude-opus-5-5")
    msgs = api.bodies[0]["messages"]
    assert msgs[1] == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "echo", "input": {"text": "a"}},
        {"type": "tool_use", "id": "t2", "name": "echo", "input": {"text": "b"}}]}
    assert msgs[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": '{"echo": "a"}'},
        {"type": "tool_result", "tool_use_id": "t2", "content": "error: boom", "is_error": True}]}


async def test_raw_cache_is_bounded():
    from iler.llm import anthropic_provider as ap

    p = Api().provider()
    for i in range(ap._RAW_CACHE_MAX + 50):
        p._remember((f"id{i}",), [{"type": "tool_use", "id": f"id{i}"}])
    assert len(p._raw) == ap._RAW_CACHE_MAX
    assert "id0" not in p._raw and f"id{ap._RAW_CACHE_MAX + 49}" in p._raw


async def test_refusal_raises_non_retryable():
    api = Api((200, _message([], stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber"})))
    with pytest.raises(LlmError) as ei:
        await api.provider().complete(MSGS, [], "claude-opus-5-5")
    assert ei.value.retryable is False
    assert "refusal" in str(ei.value)


async def test_auth_error_not_retryable_and_key_redacted():
    api = Api(_err(401, "authentication_error", f"invalid x-api-key {KEY}"))
    with pytest.raises(LlmError) as ei:
        await api.provider().complete(MSGS, [], "claude-opus-5-5")
    assert ei.value.retryable is False
    assert "401" in str(ei.value) and KEY not in str(ei.value)
    assert len(api.requests) == 1


async def test_rate_limit_and_overload_are_retryable():
    api = Api(_err(429, "rate_limit_error", "slow down"), _err(529, "overloaded_error", "overloaded"))
    p = api.provider()
    for _ in range(2):
        with pytest.raises(LlmError) as ei:
            await p.complete(MSGS, [], "claude-opus-5-5")
        assert ei.value.retryable is True


async def test_bad_request_not_retryable():
    api = Api(_err(400, "invalid_request_error", "bad model"))
    with pytest.raises(LlmError) as ei:
        await api.provider().complete(MSGS, [], "claude-opus-5-5")
    assert ei.value.retryable is False


async def test_connection_error_retryable():
    def boom(request):
        raise httpx2.ConnectError("no route")

    p = AnthropicProvider(KEY, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(boom)))
    p._client = p._client.with_options(max_retries=0)
    with pytest.raises(LlmError) as ei:
        await p.complete(MSGS, [], "claude-opus-5-5")
    assert ei.value.retryable is True


async def test_router_fails_over_to_anthropic_emergency(pool, factory):
    api = Api((200, _message([{"type": "text", "text": "claude here"}])))
    claude = api.provider()
    factory(openai=FakeLLM([LlmError("down"), LlmError("down")], name="openai"), anthropic=claude)
    s = LlmSettings(provider="openai", openai_fallback_model="backup", emergency_provider="anthropic",
                    anthropic_model="claude-sonnet-5-5")
    res = await router.complete_with_tools(MSGS, [], _ctx(), s)
    assert res.text == "claude here"
    assert api.bodies[0]["model"] == "claude-sonnet-5-5"
    rows = await pool.fetch("SELECT provider FROM ai_requests ORDER BY id")
    assert [r["provider"] for r in rows] == ["openai", "openai", "anthropic"]


async def test_router_fails_over_from_anthropic_refusal(pool, factory):
    api = Api((200, _message([], stop_reason="refusal")))
    factory(anthropic=api.provider(), gemini=FakeLLM([LlmResponse(text="gemini here")], name="gemini"))
    s = LlmSettings(provider="anthropic", emergency_provider="gemini")
    res = await router.complete_with_tools(MSGS, [], _ctx(), s)
    assert res.text == "gemini here"


async def test_check_key_via_default_factory_applies_effort(monkeypatch):
    from iler.config import get_settings

    monkeypatch.setattr(get_settings(), "anthropic_key", KEY)  # ILER's key comes from the environment
    router.set_provider_factory(None)
    api = Api((200, _message([{"type": "text", "text": "pong"}])), _err(401, "authentication_error", "bad"))
    router._provider_cache[("anthropic", KEY, "")] = api.provider()
    try:
        s = LlmSettings(provider="anthropic", anthropic_effort="high")
        await router.check_key(s)
        assert api.bodies[0]["model"] == "claude-sonnet-5-5"
        assert api.bodies[0]["output_config"] == {"effort": "high"}
        with pytest.raises(LlmError) as ei:
            await router.check_key(s)
        assert ei.value.retryable is False and KEY not in str(ei.value)
    finally:
        router._provider_cache.pop(("anthropic", KEY, ""), None)


async def test_check_key_missing_anthropic_key():
    router.set_provider_factory(None)
    with pytest.raises(LlmError):
        await router.check_key(LlmSettings(provider="anthropic"))


def test_settings_defaults():
    s = LlmSettings()
    assert s.anthropic_model == "claude-sonnet-5-5" and s.anthropic_effort == "low"
    assert LlmSettings(provider="anthropic", emergency_provider="anthropic").provider == "anthropic"
