"""OpenAI Chat Completions provider with native function calling."""

import json
import re
import time

import httpx
import openai

from iler.llm.base import ChatMessage, LlmError, LlmResponse, ToolCall, body_error_type
from iler.tools.base import Tool

TIMEOUT_S = 30.0
MAX_RETRIES = 2  # the SDK retries 429 / 5xx / connection errors with backoff


# chat-capable families only: drop audio/realtime/image/embedding/... and dated snapshots
_LIST_EXCLUDE = ("audio", "realtime", "transcribe", "tts", "image", "search", "embedding", "instruct",
                 "moderation", "codex", "computer", "deep-research")
_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_PRO = re.compile(r"-pro(-|$)")  # "-pro" models are served only by the Responses API, not Chat Completions


def chat_model_ids(ids: list[str]) -> list[str]:
    keep = {i for i in ids if i.startswith("gpt-") and not any(x in i for x in _LIST_EXCLUDE)
            and not _SNAPSHOT.search(i) and not _PRO.search(i)}
    return sorted(keep, reverse=True)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def to_openai_messages(messages: list[ChatMessage]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id})
        elif m.role == "assistant" and m.tool_calls:
            out.append({
                "role": "assistant",
                "content": m.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                    }
                    for tc in m.tool_calls
                ],
            })
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def to_openai_tools(tools: list[Tool]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]


def _parse_args(raw: str | None) -> dict:
    try:
        val = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, *, http_client: httpx.AsyncClient | None = None, base_url: str | None = None):
        self._key = api_key
        self._client = openai.AsyncOpenAI(
            api_key=api_key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES, http_client=http_client, base_url=base_url
        )

    async def list_models(self) -> list[str]:
        """Chat models available to this key (GET /v1/models); also validates the key without spending tokens."""
        try:
            ids = [m.id async for m in self._client.models.list()]
        except openai.APIStatusError as e:
            raise LlmError(_redact(f"openai HTTP {e.status_code}: {e.message}", self._key), retryable=False,
                           status=e.status_code, error_type=body_error_type(e.body)) from None
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            raise LlmError(f"openai connection error: {type(e).__name__}", retryable=True) from None
        except Exception as e:  # noqa: BLE001 - never leak SDK exceptions
            raise LlmError(_redact(f"openai error: {type(e).__name__}: {e}", self._key), retryable=True) from None
        return chat_model_ids(ids)

    async def complete(self, messages: list[ChatMessage], tools: list[Tool], model: str) -> LlmResponse:
        kwargs: dict = {"model": model, "messages": to_openai_messages(messages)}
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
        started = time.monotonic()
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as e:
            retryable = e.status_code == 429 or e.status_code >= 500
            raise LlmError(_redact(f"openai HTTP {e.status_code}: {e.message}", self._key), retryable=retryable,
                           status=e.status_code, error_type=body_error_type(e.body)) from None
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            raise LlmError(f"openai connection error: {type(e).__name__}", retryable=True) from None
        except Exception as e:  # noqa: BLE001 - never leak SDK exceptions
            raise LlmError(_redact(f"openai error: {type(e).__name__}: {e}", self._key), retryable=True) from None
        latency = int((time.monotonic() - started) * 1000)

        try:
            msg = resp.choices[0].message
            calls = [
                ToolCall(id=tc.id, name=tc.function.name, arguments=_parse_args(tc.function.arguments))
                for tc in (msg.tool_calls or [])
                if getattr(tc, "function", None) is not None
            ]
            usage = resp.usage
            return LlmResponse(
                text=msg.content or "",
                tool_calls=calls,
                model=resp.model or model,
                provider=self.name,
                input_tokens=(usage.prompt_tokens or 0) if usage else 0,
                output_tokens=(usage.completion_tokens or 0) if usage else 0,
                latency_ms=latency,
            )
        except (AttributeError, IndexError, TypeError) as e:
            raise LlmError(f"openai: unexpected response shape: {type(e).__name__}", retryable=True) from None
