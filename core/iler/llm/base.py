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
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class LlmProvider(Protocol):
    name: str

    async def complete(self, messages: list[ChatMessage], tools: list[Tool], model: str) -> LlmResponse: ...


def key_rejected_ru() -> str:
    return f"Ключ {brand_name()} отклонён провайдером — обратитесь в {brand_company()}"


_HTTP_ERR = re.compile(r"\b(anthropic|openai|gemini) HTTP (\d{3})\b([^;]*)")


def public_error(message: str) -> str:
    """Error text that is safe to show in the admin UI.

    A provider's 401/403 (or Gemini's 400 "API key not valid") may echo a masked fragment of ILER's own
    key, so such errors become a generic "key rejected" message that keeps only the provider names."""
    rejected: list[str] = []
    for m in _HTTP_ERR.finditer(message):
        provider, code, rest = m.group(1), int(m.group(2)), m.group(3)
        if code in (401, 403) or (code == 400 and re.search(r"api[ _]?key", rest, re.IGNORECASE)):
            if provider not in rejected:
                rejected.append(provider)
    if rejected:
        return f"{', '.join(rejected)}: {key_rejected_ru()}"
    return message
