"""Provider-neutral LLM types."""

import re
from dataclasses import dataclass, field
from typing import Literal, Protocol

from iler.config import brand_company, brand_name
from iler.tools.base import Tool


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


@dataclass
class LlmResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class LlmError(Exception):
    """A failed LLM call. ``status`` is the HTTP status and ``error_type`` the ``error.type`` of the
    response body when the provider (or the LLM gateway) answered with an error."""

    def __init__(self, message: str, retryable: bool = True, *, status: int | None = None,
                 error_type: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.error_type = error_type


def body_error_type(body: object) -> str | None:
    """``error.type`` of an error body, whether the SDK kept the envelope (``{"error": {"type": ...}}``)
    or already unwrapped it (``{"type": ...}``)."""
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and isinstance(inner.get("type"), str):
            return inner["type"]
        if isinstance(body.get("type"), str):
            return body["type"]
    return None


class LlmProvider(Protocol):
    name: str

    async def complete(self, messages: list[ChatMessage], tools: list[Tool], model: str) -> LlmResponse: ...


def key_rejected_ru() -> str:
    return f"Провайдер модели отклонил ключ доступа. Обратитесь в {brand_company()}."


_HTTP_ERR = re.compile(r"\b(anthropic|openai|gemini) HTTP (\d{3})\b([^;]*)")


def rejected_providers(message: str) -> list[str]:
    """Providers whose 401/403 (or Gemini's 400 "API key not valid") appears in an LlmError message."""
    rejected: list[str] = []
    for m in _HTTP_ERR.finditer(message):
        provider, code, rest = m.group(1), int(m.group(2)), m.group(3)
        if code in (401, 403) or (code == 400 and re.search(r"api[ _]?key", rest, re.IGNORECASE)):
            if provider not in rejected:
                rejected.append(provider)
    return rejected


def public_error(message: str) -> str:
    """Error text that is safe to show in the admin UI.

    A provider's 401/403 (or Gemini's 400 "API key not valid") may echo a masked fragment of ILER's own
    key, so such errors become a generic "key rejected" message that keeps only the provider names."""
    rejected = rejected_providers(message)
    if rejected:
        return f"{', '.join(rejected)}: {key_rejected_ru()}"
    return message
