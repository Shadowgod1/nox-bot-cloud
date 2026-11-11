"""OpenAI Chat Completions provider with native function calling."""

import json
import time

import httpx
import openai

from iler.llm.base import ChatMessage, LlmError, LlmResponse, ToolCall
from iler.tools.base import Tool

TIMEOUT_S = 30.0
MAX_RETRIES = 2  # the SDK retries 429 / 5xx / connection errors with backoff


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

    async def complete(self, messages: list[ChatMessage], tools: list[Tool], model: str) -> LlmResponse:
        kwargs: dict = {"model": model, "messages": to_openai_messages(messages)}
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
        started = time.monotonic()
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as e:
            retryable = e.status_code == 429 or e.status_code >= 500
            raise LlmError(_redact(f"openai HTTP {e.status_code}: {e.message}", self._key), retryable=retryable) from None
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
