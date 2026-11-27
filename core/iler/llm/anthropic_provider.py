"""Anthropic (Claude) Messages API provider with native tool use.

Notes for the Claude 5.5 models (the bot uses Sonnet 5.5 or Haiku 5.5):
- thinking is always on and cannot be disabled; depth is controlled by ``output_config.effort`` only
  (no ``thinking`` / ``budget_tokens`` / ``temperature`` / ``top_p`` / ``top_k`` are sent);
- ``tool_choice`` must stay ``auto`` (forced ``any`` / ``tool`` is rejected with 400);
- an assistant turn with ``tool_use`` must be echoed back unchanged (thinking blocks included) on the
  next request of the tool loop, so the raw content of such turns is cached per tool_use id;
- a policy decline is HTTP 200 with ``stop_reason == "refusal"``; server-side fallbacks
  (``fallbacks: "default"``, beta ``server-side-fallback-2026-07-01``) retry it on another model first;
- prompt caching (``cache_layout``): a breakpoint on the stable system head (tools + rules + business +
  prices, shared by every conversation of the install) and one on the last dialogue message; the
  per-request tail (time, client card: after SYSTEM_DYNAMIC_MARK) goes after the dialogue as a
  mid-conversation system message, so neither the head nor the earlier dialogue ever changes.
"""

import time
from collections import OrderedDict

import anthropic

from iler.llm.base import SYSTEM_DYNAMIC_MARK, ChatMessage, LlmError, LlmResponse, ToolCall, body_error_type
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
CACHE ={"type": "ephemeral"}  # 5-minute TTL: a WhatsApp dialogue and a busy bot re-read well within it
# Lead-in of the mid-conversation system message. Measured on Haiku 5.5 (27.11.2026): a bare
# "Сейчас: 27.11.2026 13:00 ..." message was ignored ("у меня нет данных о текущей дате"), so "завтра" never
# became a date; with this line the model reads the time and the client card.
TAIL_LEAD = "Служебные данные для этого ответа (от оператора, не от клиента):\n"


def system_blocks(system: str) -> list[dict]:
    """The system prompt as content blocks: the stable part with a cache breakpoint, then the per-request tail."""
    head, mark, tail = system.rpartition(SYSTEM_DYNAMIC_MARK)
    if not mark or not head.strip():
        return [{"type": "text", "text": system, "cache_control": CACHE}]
    return [{"type": "text", "text": head, "cache_control": CACHE}, {"type": "text", "text": tail}]


def split_system(system: str) -> tuple[str, str]:
    """(stable head, per-request tail: the current time and the client card); tail is "" without the mark."""
    head, mark, tail = system.rpartition(SYSTEM_DYNAMIC_MARK)
    if not mark or not head.strip():
        return system, ""
    return head, tail


def cache_layout(system: str | None, msgs: list[dict], *, tail_as_message: bool) -> tuple[list[dict] | None, list[dict]]:
    """Request layout for prompt caching.

    - the stable system head carries a breakpoint (tools + rules + business: shared by every conversation);
    - the last dialogue message carries a breakpoint, so the next turn re-reads the whole earlier dialogue;
    - the per-request tail (time, client card) goes AFTER the dialogue as a mid-conversation system message,
      so it never changes the cached prefix. ``tail_as_message=False`` (a model that rejects such messages)
      keeps it as a second system block, as before.
    """
    msgs = [dict(m) for m in msgs]
    if msgs:
        last = msgs[-1]
        content = last["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [dict(b) for b in content]
        if blocks:
            blocks[-1] = {**blocks[-1], "cache_control": CACHE}
            last["content"] = blocks
    if not system:
        return None, msgs
    head, tail = split_system(system)
    if not tail:
        return [{"type": "text", "text": head, "cache_control": CACHE}], msgs
    if tail_as_message and msgs and msgs[-1]["role"] == "user":
        return [{"type": "text", "text": head, "cache_control": CACHE}], [*msgs, {"role": "system", "content": TAIL_LEAD + tail}]
    return [{"type": "text", "text": head, "cache_control": CACHE}, {"type": "text", "text": tail}], msgs


def _rejects_system_messages(e: Exception) -> bool:
    return "role 'system'" in str(getattr(e, "message", "") or e)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def supports_fallbacks(model: str) -> bool:
    return model.startswith(_FALLBACK_MODEL_PREFIXES)


def to_anthropic_tools(tools: list[Tool]) -> list[dict]:
    return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, effort: str = "low", http_client=None, base_url: str | None = None,
                 native_key_header: bool = False):
        """``native_key_header``: also send the key as ``x-api-key`` (the LLM gateway reads the key from
        the provider's native header; anthropic 2.x itself sends it only as ``Authorization: Bearer``)."""
        self._key = api_key
        self.effort = effort  # refreshed by the router factory from LlmSettings.anthropic_effort
        # http_client must be an httpx2.AsyncClient (anthropic>=1 is built on httpx2, not httpx)
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES, http_client=http_client,
            base_url=base_url or DEFAULT_BASE_URL,
            default_headers={"x-api-key": api_key} if native_key_header else None,
        )
        # tool_use id -> (all tool_use ids of that turn, raw assistant content as returned by the API)
        self._raw: OrderedDict[str, tuple[tuple[str, ...], list[dict]]] = OrderedDict()
        # the per-request prompt tail as a mid-conversation system message (off after a 400 that rejects it)
        self._tail_as_message = True

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
        system, raw_msgs = self.to_anthropic_messages(messages)
        system_param, msgs = cache_layout(system, raw_msgs, tail_as_message=self._tail_as_message)
        kwargs: dict = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "messages": msgs,
            "output_config": {"effort": self.effort},
        }
        if system_param:
            kwargs["system"] = system_param
        if tools:
            kwargs["tools"] = to_anthropic_tools(tools)
            kwargs["tool_choice"] = {"type": "auto"}
        if supports_fallbacks(model):
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        started = time.monotonic()
        try:
            try:
                resp = await self._client.beta.messages.create(**kwargs)
            except anthropic.BadRequestError as e:
                if not (self._tail_as_message and _rejects_system_messages(e)):
                    raise
                # a model without mid-conversation system messages: the tail goes back into the system prompt
                self._tail_as_message = False
                system_param, kwargs["messages"] = cache_layout(system, raw_msgs, tail_as_message=False)
                if system_param:
                    kwargs["system"] = system_param
                resp = await self._client.beta.messages.create(**kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LlmError(_redact(f"anthropic HTTP {e.status_code}: {e.message}", self._key), retryable=False,
                           status=e.status_code, error_type=body_error_type(e.body)) from None
        except anthropic.RateLimitError as e:
            raise LlmError(_redact(f"anthropic HTTP 429: {e.message}", self._key), retryable=True,
                           status=429, error_type=body_error_type(e.body)) from None
        except anthropic.APIStatusError as e:  # other non-2xx: 5xx / 529 overloaded are transient, 4xx are not
            retryable = e.status_code == 429 or e.status_code >= 500
            raise LlmError(_redact(f"anthropic HTTP {e.status_code}: {e.message}", self._key),
                           retryable=retryable, status=e.status_code, error_type=body_error_type(e.body)) from None
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
            input_tokens = cache_read = cache_write = 0
            if usage is not None:
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
                input_tokens = (usage.input_tokens or 0) + cache_read + cache_write
            return LlmResponse(
                text="".join(texts),
                tool_calls=calls,
                model=resp.model or model,
                provider=self.name,
                input_tokens=input_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                output_tokens=(usage.output_tokens or 0) if usage is not None else 0,
                latency_ms=latency,
            )
        except LlmError:
            raise
        except (AttributeError, TypeError) as e:
            raise LlmError(f"anthropic: unexpected response shape: {type(e).__name__}", retryable=True) from None
