"""Scriptable fake LLM provider for tests (and the ILER_LLM_FAKE e2e mode)."""

from iler.llm.base import ChatMessage, LlmError, LlmResponse
from iler.tools.base import Tool


class FakeLLM:
    """Returns scripted responses in order.

    Each script item is either an ``LlmResponse`` (returned as-is, possibly with tool calls)
    or an ``Exception`` instance (raised). When the script is exhausted, ``default`` is returned
    (a ``str`` becomes a plain text response); without a default an ``LlmError`` is raised.
    Every call is recorded in ``self.calls`` as ``{"messages", "tools", "model"}``.
    """

    def __init__(
        self,
        script: list[LlmResponse | Exception] | None = None,
        *,
        default: LlmResponse | str | None = None,
        name: str = "fake",
    ):
        self.name = name
        self.script: list[LlmResponse | Exception] = list(script or [])
        self.default = default
        self.calls: list[dict] = []

    async def complete(self, messages: list[ChatMessage], tools: list[Tool], model: str) -> LlmResponse:
        self.calls.append({"messages": list(messages), "tools": list(tools), "model": model})
        if self.script:
            item = self.script.pop(0)
        elif self.default is not None:
            item = self.default if isinstance(self.default, LlmResponse) else LlmResponse(text=self.default)
        else:
            raise LlmError("FakeLLM script exhausted", retryable=False)
        if isinstance(item, Exception):
            raise item
        return LlmResponse(
            text=item.text,
            tool_calls=list(item.tool_calls),
            model=item.model or model,
            provider=item.provider or self.name,
            input_tokens=item.input_tokens,
            output_tokens=item.output_tokens,
            latency_ms=item.latency_ms,
        )
