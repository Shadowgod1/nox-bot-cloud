import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from iler.llm import router
from iler.llm.base import ChatMessage, LlmError, LlmResponse, ToolCall
from iler.llm.fake import FakeLLM
from iler.llm.gemini_provider import GeminiProvider
from iler.llm.openai_provider import OpenAIProvider
from iler.settings_models import BotSettings, LlmSettings
from iler.tools.base import ToolContext, ToolResult


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


class BoomTool(EchoTool):
    name = "boom"

    async def run(self, ctx, args):
        raise RuntimeError("kaput")


def _ctx(conversation_id=None):
    return ToolContext(conversation_id=conversation_id, phone="77010000000", settings=BotSettings(),
                       now=datetime.now(timezone.utc), dry_run=True)


def _tc(name, args, id_="c1"):
    return LlmResponse(text="", tool_calls=[ToolCall(id=id_, name=name, arguments=args)])


MSGS = [ChatMessage(role="system", content="sys"), ChatMessage(role="user", content="hi")]


@pytest.fixture
def use_fakes():
    """Route provider construction to FakeLLM instances keyed by provider name."""
    fakes: dict[str, FakeLLM] = {}

    def install(**by_provider):
        fakes.update(by_provider)
        router.set_provider_factory(lambda name, settings: fakes[name])
        return fakes

    yield install
    router.set_provider_factory(None)


async def test_tool_loop_executes_and_returns_final_text(pool, use_fakes):
    fake = FakeLLM([_tc("echo", {"text": "x"}), LlmResponse(text="готово")])
    use_fakes(openai=fake)
    tool = EchoTool()
    res = await router.complete_with_tools(MSGS, [tool], _ctx(), LlmSettings())
    assert res.text == "готово"
    assert tool.calls == [{"text": "x"}]
    assert res.tool_calls == [{"name": "echo", "args": {"text": "x"}, "result": {"echo": "x"}, "error": None}]
    assert len(res.requests) == 2
    # second call sees the assistant tool call + tool result (JSON string)
    second = fake.calls[1]["messages"]
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].name == "echo"
    assert second[-1].role == "tool" and second[-1].tool_call_id == "c1"
    assert json.loads(second[-1].content) == {"echo": "x"}
    assert fake.calls[0]["tools"] == [tool]
    # logged
    reqs = await pool.fetch("SELECT * FROM ai_requests ORDER BY id")
    assert len(reqs) == 2 and reqs[0]["conversation_id"] is None and reqs[0]["error"] is None
    calls = await pool.fetch("SELECT * FROM tool_calls")
    assert len(calls) == 1 and calls[0]["ai_request_id"] == reqs[0]["id"]
    assert calls[0]["name"] == "echo" and calls[0]["args"] == {"text": "x"} and calls[0]["result"] == {"echo": "x"}


async def test_logs_conversation_id(pool, use_fakes):
    conv_id = await pool.fetchval(
        "INSERT INTO conversations (chat_jid, phone) VALUES ('1@s.whatsapp.net', '1') RETURNING id")
    use_fakes(openai=FakeLLM([LlmResponse(text="ok", model="m", input_tokens=5, output_tokens=2)]))
    await router.complete_with_tools(MSGS, [], _ctx(conv_id), LlmSettings())
    row = await pool.fetchrow("SELECT * FROM ai_requests")
    assert row["conversation_id"] == conv_id and row["input_tokens"] == 5 and row["output_tokens"] == 2


async def test_tool_loop_stops_after_4_iterations(pool, use_fakes):
    fake = FakeLLM([_tc("echo", {"text": str(i)}, f"c{i}") for i in range(10)])
    use_fakes(openai=fake)
    tool = EchoTool()
    res = await router.complete_with_tools(MSGS, [tool], _ctx(), LlmSettings())
    assert len(tool.calls) == 4
    assert len(fake.calls) == 5  # 4 tool rounds + final call without tools
    assert fake.calls[-1]["tools"] == []
    assert res.text  # stub phrase when the model produced no text
    assert len(res.tool_calls) == 4


async def test_tool_exception_reported_to_model_not_raised(pool, use_fakes):
    fake = FakeLLM([_tc("boom", {}), LlmResponse(text="извините")])
    use_fakes(openai=fake)
    res = await router.complete_with_tools(MSGS, [BoomTool()], _ctx(), LlmSettings())
    assert res.text == "извините"
    tool_msg = fake.calls[1]["messages"][-1]
    assert tool_msg.role == "tool" and tool_msg.content.startswith("error: ") and "kaput" in tool_msg.content
    assert res.tool_calls[0]["error"] and res.tool_calls[0]["result"] is None
    row = await pool.fetchrow("SELECT * FROM tool_calls")
    assert "kaput" in row["error"]


async def test_unknown_tool_reported_to_model(pool, use_fakes):
    fake = FakeLLM([_tc("nope", {}), LlmResponse(text="ok")])
    use_fakes(openai=fake)
    res = await router.complete_with_tools(MSGS, [EchoTool()], _ctx(), LlmSettings())
    assert res.text == "ok"
    assert fake.calls[1]["messages"][-1].content.startswith("error: ")


async def test_fallback_model_used_on_error(pool, use_fakes):
    fake = FakeLLM([LlmError("503"), LlmResponse(text="from fallback")])
    use_fakes(openai=fake)
    s = LlmSettings(openai_model="main", openai_fallback_model="backup")
    res = await router.complete_with_tools(MSGS, [], _ctx(), s)
    assert res.text == "from fallback"
    assert [c["model"] for c in fake.calls] == ["main", "backup"]
    rows = await pool.fetch("SELECT model, error FROM ai_requests ORDER BY id")
    assert rows[0]["model"] == "main" and rows[0]["error"] and rows[1]["error"] is None


async def test_emergency_provider_used_when_primary_fails(pool, use_fakes):
    primary = FakeLLM([LlmError("down"), LlmError("down")], name="openai")
    emergency = FakeLLM([LlmResponse(text="gemini here")], name="gemini")
    use_fakes(openai=primary, gemini=emergency)
    s = LlmSettings(provider="openai", openai_fallback_model="backup", emergency_provider="gemini",
                    gemini_model="gem")
    res = await router.complete_with_tools(MSGS, [], _ctx(), s)
    assert res.text == "gemini here"
    assert [c["model"] for c in emergency.calls] == ["gem"]
    rows = await pool.fetch("SELECT provider FROM ai_requests ORDER BY id")
    assert [r["provider"] for r in rows] == ["openai", "openai", "gemini"]


async def test_all_providers_fail_hands_over(pool, use_fakes):
    use_fakes(openai=FakeLLM([LlmError("x")] * 5), gemini=FakeLLM([LlmError("y")] * 5, name="gemini"))
    s = LlmSettings(openai_fallback_model="backup", emergency_provider="gemini")
    with pytest.raises(LlmError) as ei:
        await router.complete_with_tools(MSGS, [], _ctx(), s)
    assert ei.value.retryable is False
    assert await pool.fetchval("SELECT count(*) FROM ai_requests WHERE error IS NOT NULL") == 3


async def test_unexpected_provider_exception_is_wrapped(pool, use_fakes):
    use_fakes(openai=FakeLLM([ValueError("sdk blew up")]))
    with pytest.raises(LlmError):
        await router.complete_with_tools(MSGS, [], _ctx(), LlmSettings())


async def test_llm_fake_env(pool, monkeypatch):
    from iler import config

    monkeypatch.setattr(config.get_settings(), "llm_fake", True)
    res = await router.complete_with_tools(MSGS, [EchoTool()], _ctx(), LlmSettings())
    assert res.text == "Здравствуйте! Чем помочь?"
    await router.check_key(LlmSettings())  # no key needed in fake mode


async def test_check_key(use_fakes):
    use_fakes(openai=FakeLLM([LlmResponse(text="pong")]))
    await router.check_key(LlmSettings())
    use_fakes(openai=FakeLLM([LlmError("401 invalid key", retryable=False)]))
    with pytest.raises(LlmError):
        await router.check_key(LlmSettings())


async def test_missing_key_raises_llm_error():
    router.set_provider_factory(None)
    with pytest.raises(LlmError):
        await router.check_key(LlmSettings(provider="openai"))
    with pytest.raises(LlmError):
        await router.check_key(LlmSettings(provider="gemini"))


def _openai_body(**message):
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "gpt-test",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


async def test_openai_tool_schema_mapping():
    seen = []

    def handler(request: httpx.Request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_openai_body(content=None, tool_calls=[
            {"id": "call_1", "type": "function", "function": {"name": "echo", "arguments": '{"text": "hi"}'}}]))

    p = OpenAIProvider("sk-test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    history = MSGS + [
        ChatMessage(role="assistant", content="", tool_calls=[ToolCall(id="c0", name="echo", arguments={"text": "a"})]),
        ChatMessage(role="tool", content='{"echo": "a"}', tool_call_id="c0"),
    ]
    resp = await p.complete(history, [EchoTool()], "gpt-test")
    body = seen[0]
    assert body["model"] == "gpt-test"
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "echo"
    assert body["tools"][0]["function"]["parameters"] == EchoTool.parameters
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][2]["tool_calls"][0]["function"] == {"name": "echo", "arguments": '{"text": "a"}'}
    assert body["messages"][3] == {"role": "tool", "content": '{"echo": "a"}', "tool_call_id": "c0"}
    assert resp.tool_calls == [ToolCall(id="call_1", name="echo", arguments={"text": "hi"})]
    assert resp.provider == "openai" and resp.input_tokens == 11 and resp.output_tokens == 3


async def test_openai_no_tools_omits_param_and_retries_429():
    seen = []

    def handler(request: httpx.Request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            return httpx.Response(429, json={"error": {"message": "rate"}}, headers={"retry-after-ms": "1"})
        return httpx.Response(200, json=_openai_body(content="hello"))

    p = OpenAIProvider("sk-test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    resp = await p.complete(MSGS, [], "gpt-test")
    assert resp.text == "hello" and len(seen) == 2
    assert "tools" not in seen[0]


async def test_openai_auth_error_wrapped_not_retryable():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    p = OpenAIProvider("sk-bad", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(LlmError) as ei:
        await p.complete(MSGS, [], "gpt-test")
    assert ei.value.retryable is False and len(calls) == 1
    assert "sk-bad" not in str(ei.value)


async def test_gemini_function_call_mapping():
    from google.genai import types

    fc = types.FunctionCall(name="echo", args={"text": "hi"})
    fake_resp = SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part(function_call=fc)]))],
        usage_metadata=SimpleNamespace(prompt_token_count=7, candidates_token_count=2),
    )
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=fake_resp)
    p = GeminiProvider("g-key", client=client)
    history = MSGS + [
        ChatMessage(role="assistant", content="", tool_calls=[ToolCall(id="c0", name="echo", arguments={"text": "a"})]),
        ChatMessage(role="tool", content='{"echo": "a"}', tool_call_id="c0"),
    ]
    resp = await p.complete(history, [EchoTool()], "gemini-test")

    kw = client.aio.models.generate_content.call_args.kwargs
    assert kw["model"] == "gemini-test"
    cfg = kw["config"]
    assert cfg.system_instruction == "sys"
    decl = cfg.tools[0].function_declarations[0]
    assert decl.name == "echo" and decl.parameters_json_schema == EchoTool.parameters
    contents = kw["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].function_call.name == "echo"
    fr = contents[2].parts[0].function_response
    assert fr.name == "echo" and fr.response == {"echo": "a"}
    assert resp.tool_calls[0].name == "echo" and resp.tool_calls[0].arguments == {"text": "hi"}
    assert resp.tool_calls[0].id
    assert resp.provider == "gemini" and resp.input_tokens == 7 and resp.output_tokens == 2


async def test_gemini_sdk_error_wrapped():
    from google.genai import errors

    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(
        side_effect=errors.ClientError(400, {"error": {"message": "API key not valid", "status": "INVALID_ARGUMENT"}}))
    p = GeminiProvider("g-key", client=client)
    with pytest.raises(LlmError) as ei:
        await p.complete(MSGS, [], "gemini-test")
    assert ei.value.retryable is False


def test_openai_chat_model_filter():
    from iler.llm.openai_provider import chat_model_ids

    ids = ["gpt-5.4-mini", "gpt-5.4-mini-2026-03-17", "gpt-realtime", "gpt-5.4-nano", "text-embedding-3-small",
           "gpt-image-1", "gpt-4o-mini-tts", "o3", "gpt-5.5"]
    assert chat_model_ids(ids) == ["gpt-5.5", "gpt-5.4-nano", "gpt-5.4-mini"]


def test_gemini_chat_model_filter():
    from iler.llm.gemini_provider import chat_model_ids

    models = [SimpleNamespace(name="models/gemini-3.8-flash", supported_actions=["generateContent", "countTokens"]),
              SimpleNamespace(name="models/gemini-embedding-001", supported_actions=["embedContent"]),
              SimpleNamespace(name="models/gemini-3.8-flash-image", supported_actions=["generateContent"]),
              SimpleNamespace(name="models/gemini-3.5-flash-lite", supported_actions=["generateContent"]),
              SimpleNamespace(name="models/imagen-4", supported_actions=["predict"])]
    assert chat_model_ids(models) == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]


async def test_list_models_claude_is_fixed_and_bad_key_raises():
    assert await router.list_models(LlmSettings(provider="anthropic")) == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    with pytest.raises(LlmError):
        await router.list_models(LlmSettings(provider="openai"))  # no key


async def test_chain_skips_providers_without_ilER_key(monkeypatch):
    from iler.config import get_settings

    cfg = get_settings()
    monkeypatch.setattr(cfg, "anthropic_key", "sk-ant-vendor")
    monkeypatch.setattr(cfg, "openai_key", "")
    monkeypatch.setattr(cfg, "gemini_key", "")
    chain = router._chain(LlmSettings(provider="openai", emergency_provider="gemini"))
    assert chain == [("anthropic", "claude-sonnet-5-5")]
    assert router.available_providers() == ["anthropic"]
