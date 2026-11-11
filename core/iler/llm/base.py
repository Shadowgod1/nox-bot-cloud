"""Provider-neutral LLM types."""

from dataclasses import dataclass, field
from typing import Literal, Protocol

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
