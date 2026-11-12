"""LLM routing: provider chain (model -> fallback model -> emergency provider) and the tool loop.

Every LLM attempt is logged to ``ai_requests`` and every executed tool call to ``tool_calls``.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from iler import config
from iler.llm.base import ChatMessage, LlmError, LlmProvider, LlmResponse
from iler.llm.fake import FakeLLM
from iler.settings_models import LlmSettings
from iler.tools.base import Tool, ToolContext

log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 4
FAKE_REPLY = "Здравствуйте! Чем помочь?"
STUB_REPLY = "Извините, не получилось подготовить ответ. Уточните, пожалуйста, ваш вопрос."
_ERR_MAX = 1000

ProviderFactory = Callable[[str, LlmSettings], LlmProvider]


@dataclass
class TurnResult:
    text: str
    tool_calls: list[dict] = field(default_factory=list)  # {name, args, result, error}
    requests: list[LlmResponse] = field(default_factory=list)


_factory_override: ProviderFactory | None = None
_provider_cache: dict[tuple[str, str], LlmProvider] = {}
_fake_provider = FakeLLM(default=FAKE_REPLY)


def set_provider_factory(factory: ProviderFactory | None) -> None:
    """Test hook: build providers with ``factory(provider_name, settings)``; ``None`` restores the default."""
    global _factory_override
    _factory_override = factory


_KEY_FIELDS = {"openai": "openai_key", "gemini": "gemini_key", "anthropic": "anthropic_key"}
_MODEL_FIELDS = {"openai": "openai_model", "gemini": "gemini_model", "anthropic": "anthropic_model"}


def _default_factory(name: str, settings: LlmSettings) -> LlmProvider:
    if name not in _KEY_FIELDS:
        raise LlmError(f"unknown LLM provider: {name}", retryable=False)
    secret = getattr(settings, _KEY_FIELDS[name])
    key = secret.get_secret_value() if secret is not None else ""
    if not key:
        raise LlmError(f"{name}: API key is not configured", retryable=False)
    provider = _provider_cache.get((name, key))
    if provider is None:
        if name == "openai":
            from iler.llm.openai_provider import OpenAIProvider

            provider = OpenAIProvider(key)
        elif name == "gemini":
            from iler.llm.gemini_provider import GeminiProvider

            provider = GeminiProvider(key)
        else:
            from iler.llm.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(key)
        _provider_cache[(name, key)] = provider
    if name == "anthropic":
        provider.effort = settings.anthropic_effort  # type: ignore[attr-defined]
    return provider


def _make_provider(name: str, settings: LlmSettings) -> LlmProvider:
    if config.get_settings().llm_fake:
        return _fake_provider
    try:
        return (_factory_override or _default_factory)(name, settings)
    except LlmError:
        raise
    except Exception as e:  # noqa: BLE001
        raise LlmError(f"{name}: cannot create provider: {type(e).__name__}", retryable=False) from None


def _model_for(name: str, settings: LlmSettings) -> str:
    return getattr(settings, _MODEL_FIELDS.get(name, "openai_model"))


def _chain(settings: LlmSettings) -> list[tuple[str, str]]:
    """Ordered (provider, model) attempts."""
    if config.get_settings().llm_fake:
        return [("fake", "fake")]
    chain = [(settings.provider, _model_for(settings.provider, settings))]
    if settings.provider == "openai" and settings.openai_fallback_model:
        chain.append(("openai", settings.openai_fallback_model))
    if settings.emergency_provider and settings.emergency_provider != settings.provider:
        chain.append((settings.emergency_provider, _model_for(settings.emergency_provider, settings)))
    return chain


async def _log_request(conversation_id: int | None, provider: str, model: str,
                       resp: LlmResponse | None, latency_ms: int, error: str | None) -> int | None:
    try:
        from iler.db.pool import get_pool

        return await get_pool().fetchval(
            "INSERT INTO ai_requests (conversation_id, provider, model, input_tokens, output_tokens, latency_ms, error)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
            conversation_id, provider, (resp.model if resp and resp.model else model),
            resp.input_tokens if resp else 0, resp.output_tokens if resp else 0, latency_ms,
            error[:_ERR_MAX] if error else None,
        )
    except Exception as e:  # noqa: BLE001 - journaling must not break the turn
        log.warning("ai_requests logging failed: %s", type(e).__name__)
        return None


async def _log_tool_call(ai_request_id: int | None, call: dict) -> None:
    try:
        from iler.db.pool import get_pool

        await get_pool().execute(
            "INSERT INTO tool_calls (ai_request_id, name, args, result, error) VALUES ($1, $2, $3, $4, $5)",
            ai_request_id, call["name"], call["args"], call["result"],
            call["error"][:_ERR_MAX] if call["error"] else None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("tool_calls logging failed: %s", type(e).__name__)


class _Session:
    """One turn: remembers which chain entry works so later iterations stick to it."""

    def __init__(self, settings: LlmSettings, conversation_id: int | None):
        self.settings = settings
        self.conversation_id = conversation_id
        self.chain = _chain(settings)
        self.idx = 0

    async def call(self, messages: list[ChatMessage], tools: list[Tool]) -> tuple[LlmResponse, int | None]:
        errors: list[str] = []
        while self.idx < len(self.chain):
            name, model = self.chain[self.idx]
            started = time.monotonic()
            try:
                provider = _make_provider(name, self.settings)
                resp = await provider.complete(messages, tools, model)
            except Exception as e:  # noqa: BLE001 - SDK/provider errors never leak
                err = str(e) if isinstance(e, LlmError) else f"{name}: {type(e).__name__}"
                latency = int((time.monotonic() - started) * 1000)
                await _log_request(self.conversation_id, name, model, None, latency, err)
                log.warning("LLM attempt failed provider=%s model=%s: %s", name, model, err)
                errors.append(f"{name}/{model}: {err}")
                self.idx += 1
                continue
            latency = int((time.monotonic() - started) * 1000)
            if not resp.latency_ms:
                resp.latency_ms = latency
            if not resp.provider:
                resp.provider = name
            req_id = await _log_request(self.conversation_id, name, model, resp, resp.latency_ms, None)
            return resp, req_id
        raise LlmError("all LLM providers failed: " + "; ".join(errors), retryable=False)


def _serialize(content: str | dict) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


async def _run_tool(tool: Tool | None, name: str, args: dict, ctx: ToolContext) -> dict:
    if tool is None:
        return {"name": name, "args": args, "result": None, "error": f"unknown tool: {name}"}
    try:
        res = await tool.run(ctx, args)
        return {"name": name, "args": args, "result": res.content, "error": None}
    except Exception as e:  # noqa: BLE001 - fail-open: the model sees the error
        return {"name": name, "args": args, "result": None, "error": f"{type(e).__name__}: {e}"}


async def complete_with_tools(
    messages: list[ChatMessage], tools: list[Tool], ctx: ToolContext, settings: LlmSettings
) -> TurnResult:
    """Run the LLM with native tools for up to MAX_TOOL_ITERATIONS tool rounds.

    Raises ``LlmError(retryable=False)`` when every provider in the chain failed.
    """
    if config.get_settings().llm_fake:
        tools = []
    session = _Session(settings, ctx.conversation_id)
    by_name = {t.name: t for t in tools}
    history = list(messages)
    result = TurnResult(text="")

    for iteration in range(MAX_TOOL_ITERATIONS + 1):
        offer = tools if iteration < MAX_TOOL_ITERATIONS else []
        resp, req_id = await session.call(history, offer)
        result.requests.append(resp)
        if not resp.tool_calls or not offer:
            result.text = resp.text.strip() or STUB_REPLY
            return result
        history.append(ChatMessage(role="assistant", content=resp.text, tool_calls=list(resp.tool_calls)))
        for tc in resp.tool_calls:
            call = await _run_tool(by_name.get(tc.name), tc.name, tc.arguments, ctx)
            result.tool_calls.append(call)
            await _log_tool_call(req_id, call)
            content = f"error: {call['error']}" if call["error"] else _serialize(call["result"])
            history.append(ChatMessage(role="tool", content=content, tool_call_id=tc.id))
    result.text = STUB_REPLY  # unreachable: the last iteration offers no tools
    return result


async def list_models(settings: LlmSettings) -> list[str]:
    """Models the owner can pick for the primary provider. Claude is limited to Sonnet/Haiku; OpenAI and
    Gemini are listed live for the configured key (which also validates it). Raises LlmError."""
    from iler.settings_models import ANTHROPIC_MODELS

    if settings.provider == "anthropic":
        return list(ANTHROPIC_MODELS)
    if config.get_settings().llm_fake:
        return [_model_for(settings.provider, settings)]
    provider = _make_provider(settings.provider, settings)
    try:
        return await provider.list_models()  # type: ignore[attr-defined]
    except LlmError:
        raise
    except Exception as e:  # noqa: BLE001
        raise LlmError(f"{settings.provider}: {type(e).__name__}", retryable=False) from None


async def check_key(settings: LlmSettings) -> None:
    """Validate the configured primary provider key/model with a minimal request. Raises LlmError."""
    if config.get_settings().llm_fake:
        return
    provider = _make_provider(settings.provider, settings)
    try:
        await provider.complete([ChatMessage(role="user", content="ping")], [], _model_for(settings.provider, settings))
    except LlmError:
        raise
    except Exception as e:  # noqa: BLE001
        raise LlmError(f"{settings.provider}: {type(e).__name__}", retryable=False) from None
