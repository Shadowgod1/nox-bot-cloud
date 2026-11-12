"""Anthropic (Claude) Messages API provider with native tool use.

Notes for the Claude 5.5 models (the bot uses Sonnet 5.5 or Haiku 5.5):
- thinking is always on and cannot be disabled; depth is controlled by ``output_config.effort`` only
  (no ``thinking`` / ``budget_tokens`` / ``temperature`` / ``top_p`` / ``top_k`` are sent);
- ``tool_choice`` must stay ``auto`` (forced ``any`` / ``tool`` is rejected with 400);
- an assistant turn with ``tool_use`` must be echoed back unchanged (thinking blocks included) on the
  next request of the tool loop, so the raw content of such turns is cached per tool_use id;
- a policy decline is HTTP 200 with ``stop_reason == "refusal"``; server-side fallbacks
  (``fallbacks: "default"``, beta ``server-side-fallback-2026-07-01``) retry it on another model first.
"""

import time
from collections import OrderedDict

import anthropic

from iler.llm.base import ChatMessage, LlmError, LlmResponse, ToolCall
from iler.tools.base import Tool

TIMEOUT_S = 60.0
MAX_RETRIES = 2  # the SDK retries connection errors, 408/409/429 and >=500 with backoff
MAX_TOKENS = 4096  # covers the (low-effort) thinking plus a WhatsApp-sized reply
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# pinned so a stray ANTHROPIC_BASE_URL in the host environment cannot redirect the owner's key
DEFAULT_BASE_URL = "https://api.anthropic.com"
# models that accept server-side fallbacks (Haiku 5.5 rejects the parameter)
_FALLBACK_MODEL_PREFIXES = ("claude-opus-5-5", "claude-sonnet-5-5")
_RAW_CACHE_MAX = 500
_ERROR_PREFIX = "error: "  # how router.complete_with_tools reports a failed tool to the model


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def supports_fallbacks(model: str) -> bool:
    return model.startswith(_FALLBACK_MODEL_PREFIXES)


def to_anthropic_tools(tools: list[Tool]) -> list[dict]:
    return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, effort: str = "low", http_client=None, base_url: str | None = None):
        self._key = api_key
        self.effort = effort  # refreshed by the router factory from LlmSettings.anthropic_effort
        # http_client must be an httpx2.AsyncClient (anthropic>=1 is built on httpx2, not httpx)
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES, http_client=http_client,
            base_url=base_url or DEFAULT_BASE_URL,
        )
        # tool_use id -> (all tool_use ids of that turn, raw assistant content as returned by the API)
        self._raw: OrderedDict[str, tuple[tuple[str, ...], list[dict]]] = OrderedDict()

    # --- request mapping ------------------------------------------------------------------------

    def _remember(self, ids: tuple[str, ...], raw: list[dict]) -> None:
        for call_id in ids:
            self._raw[call_id] = (ids, raw)
            self._raw.move_to_end(call_id)
        while len(self._raw) > _RAW_CACHE_MAX:
            self._raw.popitem(last=False)

    def _assistant_content(self, m: ChatMessage) -> list[dict] | str | None:
        calls = m.tool_calls or []
        if not calls:
            return m.content or None
        ids = tuple(tc.id for tc in calls)
        cached = self._raw.get(ids[0])
        if cached is not None and cached[0] == ids:
            self._raw.move_to_end(ids[0])
            return cached[1]  # verbatim, thinking blocks included
        blocks: list[dict] = [{"type": "text", "text": m.content}] if m.content else []
        blocks += [{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments} for tc in calls]
        return blocks

    def to_anthropic_messages(self, messages: list[ChatMessage]) -> tuple[str | None, list[dict]]:
        system: list[str] = []
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    system.append(m.content)
            elif m.role == "tool":
                block: dict = {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.content}
                if m.content.startswith(_ERROR_PREFIX):
                    block["is_error"] = True
                prev = out[-1] if out else None
                if prev is not None and prev["role"] == "user" and isinstance(prev["content"], list):
                    prev["content"].append(block)  # parallel calls: all results in one user turn
                else:
                    out.append({"role": "user", "content": [block]})
            elif m.role == "assistant":
                content = self._assistant_content(m)
                if content:
                    out.append({"role": "assistant", "content": content})
            elif m.content:
                out.append({"role": "user", "content": m.content})
        while out and out[0]["role"] != "user":  # the API requires the dialogue to start with the user
            out.pop(0)
        return ("\n\n".join(system) or None), out

    # --- call ---------------------------------------------------------------------------------

    async def complete(self, messages: list[ChatMessage], tools: list[Tool], model: str) -> LlmResponse:
        system, msgs = self.to_anthropic_messages(messages)
        kwargs: dict = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "messages": msgs,
            "output_config": {"effort": self.effort},
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = to_anthropic_tools(tools)
            kwargs["tool_choice"] = {"type": "auto"}
        if supports_fallbacks(model):
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        started = time.monotonic()
        try:
            resp = await self._client.beta.messages.create(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LlmError(_redact(f"anthropic HTTP {e.status_code}: {e.message}", self._key), retryable=False) from None
        except anthropic.RateLimitError as e:
            raise LlmError(_redact(f"anthropic HTTP 429: {e.message}", self._key), retryable=True) from None
        except anthropic.APIStatusError as e:  # other non-2xx: 5xx / 529 overloaded are transient, 4xx are not
            retryable = e.status_code == 429 or e.status_code >= 500
            raise LlmError(_redact(f"anthropic HTTP {e.status_code}: {e.message}", self._key),
                           retryable=retryable) from None
        except anthropic.APIConnectionError as e:  # includes APITimeoutError
            raise LlmError(f"anthropic connection error: {type(e).__name__}", retryable=True) from None
        except Exception as e:  # noqa: BLE001 - never leak SDK exceptions
            raise LlmError(_redact(f"anthropic error: {type(e).__name__}: {e}", self._key), retryable=True) from None
        latency = int((time.monotonic() - started) * 1000)

        try:
            if resp.stop_reason == "refusal":  # check before reading content (may be empty or partial)
                details = getattr(resp, "stop_details", None)
                category = getattr(details, "category", None) if details is not None else None
                raise LlmError(f"anthropic: refusal ({category or 'unspecified'})", retryable=False)
            texts: list[str] = []
            calls: list[ToolCall] = []
            for block in resp.content or []:
                if block.type == "text":
                    texts.append(block.text)
                elif block.type == "tool_use":
                    args = block.input if isinstance(block.input, dict) else {}
                    calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(args)))
            if calls:
                self._remember(tuple(c.id for c in calls), [b.to_dict(mode="json") for b in resp.content])
            usage = resp.usage
            input_tokens = 0
            if usage is not None:
                input_tokens = ((usage.input_tokens or 0) + (usage.cache_read_input_tokens or 0)
                                + (usage.cache_creation_input_tokens or 0))
            return LlmResponse(
                text="".join(texts),
                tool_calls=calls,
                model=resp.model or model,
                provider=self.name,
                input_tokens=input_tokens,
                output_tokens=(usage.output_tokens or 0) if usage is not None else 0,
                latency_ms=latency,
            )
        except LlmError:
            raise
        except (AttributeError, TypeError) as e:
            raise LlmError(f"anthropic: unexpected response shape: {type(e).__name__}", retryable=True) from None
