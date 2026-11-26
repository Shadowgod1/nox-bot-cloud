"""LLM routing: provider chain (model -> fallback model -> emergency provider) and the tool loop.

Every LLM attempt is logged to ``ai_requests`` and every executed tool call to ``tool_calls``.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from iler import config, usage
from iler.llm import gateway
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
_provider_cache: dict[tuple[str, str, str], LlmProvider] = {}
_fake_provider = FakeLLM(default=FAKE_REPLY)


def set_provider_factory(factory: ProviderFactory | None) -> None:
    """Test hook: build providers with ``factory(provider_name, settings)``; ``None`` restores the default."""
    global _factory_override
    _factory_override = factory


PROVIDERS = ("anthropic", "openai", "gemini")
_MODEL_FIELDS = {"openai": "openai_model", "gemini": "gemini_model", "anthropic": "anthropic_model"}


def vendor_key(name: str) -> str:
    """The API key the bot sends to a provider: the gateway's virtual key in gateway mode, else ILER's own
    key (environment, set by the technician); "" when not configured."""
    if name not in PROVIDERS:
        return ""
    if gateway.enabled():
        return gateway.key()
    return getattr(config.get_settings(), f"{name}_key", "")


def provider_base_url(name: str) -> str | None:
    """Base URL of a provider client: the gateway route in gateway mode, else the optional
    ILER_<PROVIDER>_BASE_URL override; None = the SDK default."""
    if gateway.enabled():
        return gateway.base_url(name)
    return getattr(config.get_settings(), f"{name}_base_url", "").strip() or None


def available_providers() -> list[str]:
    """Providers the owner can choose: those with a key (all of them in fake mode, and all of them in
    gateway mode once the virtual key is set)."""
    if config.get_settings().llm_fake:
        return list(PROVIDERS)
    return [p for p in PROVIDERS if vendor_key(p)]


def _default_factory(name: str, settings: LlmSettings) -> LlmProvider:
    if name not in PROVIDERS:
        raise LlmError(f"unknown LLM provider: {name}", retryable=False)
    key = vendor_key(name)
    if not key:
        raise LlmError(f"{name}: ключ {config.brand_name()} не настроен (обратитесь в поддержку {config.brand_company()})", retryable=False)
    base_url = provider_base_url(name)
    provider = _provider_cache.get((name, key, base_url or ""))
    if provider is None:
        if name == "openai":
            from iler.llm.openai_provider import OpenAIProvider

            provider = OpenAIProvider(key, base_url=base_url)
        elif name == "gemini":
            from iler.llm.gemini_provider import GeminiProvider

            provider = GeminiProvider(key, base_url=base_url,
                                      api_version=gateway.GEMINI_API_VERSION if gateway.enabled() else None)
        else:
            from iler.llm.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(key, base_url=base_url, native_key_header=gateway.enabled())
        _provider_cache[(name, key, base_url or "")] = provider
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
    # a provider without an ILER key cannot work: use the first one that has a key instead
    available = available_providers()
    primary = settings.provider
    if available and primary not in available:
        primary = available[0]
    chain = [(primary, _model_for(primary, settings))]
    if primary == "openai" and settings.openai_fallback_model:
        chain.append(("openai", settings.openai_fallback_model))
    emergency = settings.emergency_provider
    if emergency and emergency != primary and (not available or emergency in available):
        chain.append((emergency, _model_for(emergency, settings)))
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
                if gateway.blocking_error(e):
                    raise  # every provider goes through the same gateway balance: no point in the fallbacks
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

    The coin limit is enforced here, so every reply turn (the tool loop) is bounded by it (AD-3); the
    connection checks ``check_key`` / ``list_models`` are deliberately outside the limit. Raises
    ``usage.CoinsExhausted`` without touching any provider when the coins are used up, and refreshes
    the coin accounting (``usage.after_spend``) after the turn, whether it succeeded or failed.
    Raises ``LlmError(retryable=False)`` when every provider in the chain failed.

    Without the gateway the local monthly limit (ILER_MONTHLY_COINS) decides. In gateway mode the gateway
    is the authority: the local limit is ignored, a 402 ``insufficient_coins`` raises ``CoinsExhausted``
    and a 401 ``key_revoked`` raises ``usage.AccessSuspended``, and later turns raise the same without a
    call until the gateway balance is back (``gateway.blocked``).
    """
    if gateway.enabled():
        kind = await gateway.blocked()
        if kind:
            raise _exhausted(kind)
    elif await usage.limit_reached():
        raise usage.CoinsExhausted()
    try:
        return await _complete_with_tools(messages, tools, ctx, settings)
    except LlmError as e:
        kind = gateway.blocking_error(e)
        if not kind:
            raise
        gateway.mark_blocked(kind)
        raise _exhausted(kind) from None
    finally:
        await usage.after_spend()


def _exhausted(kind: str) -> usage.CoinsExhausted:
    return usage.AccessSuspended() if kind == gateway.KEY_REVOKED else usage.CoinsExhausted()


async def _complete_with_tools(
    messages: list[ChatMessage], tools: list[Tool], ctx: ToolContext, settings: LlmSettings
) -> TurnResult:
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
