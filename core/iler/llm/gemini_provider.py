"""Google Gemini provider (google-genai) with function declarations."""

import json
import time
import uuid
from collections import OrderedDict

from google import genai
from google.genai import errors, types

from iler.llm.base import ChatMessage, LlmError, LlmResponse, ToolCall, body_error_type
from iler.tools.base import Tool

TIMEOUT_MS = 30_000
RETRY_STATUSES = [429, 500, 502, 503, 504]
_SIG_CACHE_MAX = 1000


_LIST_EXCLUDE = ("embedding", "image", "tts", "live", "audio", "robotics", "computer", "aqa")


def chat_model_ids(models: list) -> list[str]:
    """Text models that support generateContent, without the "models/" prefix."""
    keep = set()
    for m in models:
        name = (getattr(m, "name", "") or "").removeprefix("models/")
        actions = getattr(m, "supported_actions", None) or []
        if name.startswith("gemini") and "generateContent" in actions and not any(x in name for x in _LIST_EXCLUDE):
            keep.add(name)
    return sorted(keep, reverse=True)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _tool_response(content: str) -> dict:
    try:
        val = json.loads(content)
    except (TypeError, ValueError):
        return {"result": content}
    return val if isinstance(val, dict) else {"result": val}


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, *, client=None, base_url: str | None = None, api_version: str | None = None,
                 http_client=None):
        """``base_url`` / ``api_version`` redirect the client (LLM gateway: ``<gateway>/llm/gemini`` and
        ``v1beta``); ``http_client`` (an ``httpx.AsyncClient``) is for tests."""
        self._key = api_key
        options: dict = {
            "timeout": TIMEOUT_MS,
            "retry_options": types.HttpRetryOptions(attempts=3, http_status_codes=RETRY_STATUSES),
        }
        if base_url:
            options["base_url"] = base_url
        if api_version:
            options["api_version"] = api_version
        if http_client is not None:
            options["httpx_async_client"] = http_client
        self._client = client or genai.Client(api_key=api_key, http_options=types.HttpOptions(**options))
        # thought signatures of returned function calls, keyed by our tool call id (needed by thinking models)
        self._signatures: OrderedDict[str, bytes] = OrderedDict()

    def _contents(self, messages: list[ChatMessage]) -> tuple[str | None, list[types.Content]]:
        system: list[str] = []
        contents: list[types.Content] = []
        names: dict[str, str] = {}
        for m in messages:
            if m.role == "system":
                system.append(m.content)
            elif m.role == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=m.content)]))
            elif m.role == "assistant":
                parts: list[types.Part] = []
                if m.content:
                    parts.append(types.Part(text=m.content))
                for tc in m.tool_calls or []:
                    names[tc.id] = tc.name
                    parts.append(types.Part(
                        function_call=types.FunctionCall(name=tc.name, args=tc.arguments),
                        thought_signature=self._signatures.get(tc.id),
                    ))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            elif m.role == "tool":
                part = types.Part.from_function_response(
                    name=names.get(m.tool_call_id or "", "tool"), response=_tool_response(m.content)
                )
                prev = contents[-1] if contents else None
                if prev is not None and prev.role == "user" and prev.parts and prev.parts[0].function_response:
                    prev.parts.append(part)  # parallel calls: all responses in one turn
                else:
                    contents.append(types.Content(role="user", parts=[part]))
        return ("\n\n".join(system) or None), contents

    def _remember(self, call_id: str, signature: bytes | None) -> None:
        if not signature:
            return
        self._signatures[call_id] = signature
        while len(self._signatures) > _SIG_CACHE_MAX:
            self._signatures.popitem(last=False)

    async def list_models(self) -> list[str]:
        """Text models available to this key; also validates the key without spending tokens."""
        try:
            models = [m async for m in await self._client.aio.models.list()]
        except errors.APIError as e:
            code = getattr(e, "code", None)
            raise LlmError(_redact(f"gemini HTTP {code}: {e.message}", self._key), retryable=False,
                           status=code, error_type=body_error_type(getattr(e, "details", None))) from None
        except Exception as e:  # noqa: BLE001 - never leak SDK exceptions
            raise LlmError(_redact(f"gemini error: {type(e).__name__}: {e}", self._key), retryable=True) from None
        return chat_model_ids(models)

    async def complete(self, messages: list[ChatMessage], tools: list[Tool], model: str) -> LlmResponse:
        system, contents = self._contents(messages)
        config = types.GenerateContentConfig(system_instruction=system)
        if tools:
            config.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
                for t in tools
            ])]
            config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        started = time.monotonic()
        try:
            resp = await self._client.aio.models.generate_content(model=model, contents=contents, config=config)
        except errors.APIError as e:
            code = getattr(e, "code", 0) or 0
            retryable = code == 429 or code >= 500
            raise LlmError(_redact(f"gemini HTTP {code}: {e.message}", self._key), retryable=retryable,
                           status=code, error_type=body_error_type(getattr(e, "details", None))) from None
        except Exception as e:  # noqa: BLE001 - never leak SDK exceptions
            raise LlmError(_redact(f"gemini error: {type(e).__name__}: {e}", self._key), retryable=True) from None
        latency = int((time.monotonic() - started) * 1000)

        texts: list[str] = []
        calls: list[ToolCall] = []
        try:
            cand = (resp.candidates or [None])[0]
            parts = (cand.content.parts if cand is not None and cand.content is not None else None) or []
            for p in parts:
                if getattr(p, "function_call", None) is not None:
                    fc = p.function_call
                    call_id = fc.id or f"call_{uuid.uuid4().hex[:12]}"
                    self._remember(call_id, getattr(p, "thought_signature", None))
                    calls.append(ToolCall(id=call_id, name=fc.name or "", arguments=dict(fc.args or {})))
                elif getattr(p, "text", None) and not getattr(p, "thought", False):
                    texts.append(p.text)
            usage = resp.usage_metadata
        except (AttributeError, TypeError) as e:
            raise LlmError(f"gemini: unexpected response shape: {type(e).__name__}", retryable=True) from None
        return LlmResponse(
            text="".join(texts),
            tool_calls=calls,
            model=model,
            provider=self.name,
            input_tokens=(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0,
            output_tokens=(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0,
            latency_ms=latency,
        )
