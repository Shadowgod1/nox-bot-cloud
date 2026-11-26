"""Token usage from upstream responses (JSON bodies and SSE streams) of Anthropic, OpenAI and Gemini.

  Anthropic  message.usage: input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens;
             stream: message_start (message.usage) then message_delta (usage, cumulative; wins when present)
  OpenAI     Chat Completions usage.prompt_tokens / completion_tokens (stream: the final usage chunk);
             Responses API usage.input_tokens / output_tokens (stream: response.completed / .incomplete);
             embeddings usage.prompt_tokens. Cached prompt tokens are billed at the full input price.
  Gemini     usageMetadata.promptTokenCount, candidatesTokenCount + thoughtsTokenCount (thinking is billed as
             output by Google); streams repeat a cumulative usageMetadata, the last one wins.
"""

import json
import re
from dataclasses import dataclass, field

from nox_portal.prices import Tokens

_SSE_SPLIT = re.compile(rb"\r\n\r\n|\n\n|\r\r")


def _int(value) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class Meter:
    provider: str
    tokens: Tokens = field(default_factory=Tokens)
    found: bool = False
    model: str | None = None
    upstream_id: str | None = None

    # -- one parsed object (a JSON body, an SSE data payload or a JSON-array element) --

    def feed(self, obj) -> None:
        if isinstance(obj, list):
            for item in obj:
                self.feed(item)
            return
        if not isinstance(obj, dict):
            return
        getattr(self, f"_{self.provider}")(obj)

    def _anthropic(self, obj: dict) -> None:
        kind = obj.get("type")
        if kind == "message_start":
            msg = obj.get("message") or {}
            self._anthropic_usage(msg.get("usage"))
            self._meta(msg.get("model"), msg.get("id"))
        elif kind == "message_delta":
            self._anthropic_usage(obj.get("usage"), partial=True)
        elif kind == "message" or "usage" in obj:
            self._anthropic_usage(obj.get("usage"))
            self._meta(obj.get("model"), obj.get("id"))

    def _anthropic_usage(self, usage, *, partial: bool = False) -> None:
        if not isinstance(usage, dict):
            return
        self.found = True
        t = self.tokens
        for attr, name in (("input", "input_tokens"), ("cache_write", "cache_creation_input_tokens"),
                           ("cache_read", "cache_read_input_tokens"), ("output", "output_tokens")):
            value = usage.get(name)
            if value is not None:
                setattr(t, attr, _int(value))
            elif not partial:
                # message_delta counts are cumulative; a missing / null field keeps the message_start value
                setattr(t, attr, 0)

    def _openai(self, obj: dict) -> None:
        kind = obj.get("type")
        if isinstance(kind, str) and kind.startswith("response."):
            resp = obj.get("response")
            if isinstance(resp, dict):
                self._meta(resp.get("model"), resp.get("id"))
                self._openai_usage(resp.get("usage"))
            return
        self._meta(obj.get("model"), obj.get("id"))
        self._openai_usage(obj.get("usage"))

    def _openai_usage(self, usage) -> None:
        if not isinstance(usage, dict):
            return
        self.found = True
        if "prompt_tokens" in usage or "completion_tokens" in usage:
            self.tokens.input = _int(usage.get("prompt_tokens"))
            self.tokens.output = _int(usage.get("completion_tokens"))
        else:
            self.tokens.input = _int(usage.get("input_tokens"))
            self.tokens.output = _int(usage.get("output_tokens"))

    def _gemini(self, obj: dict) -> None:
        self._meta(obj.get("modelVersion"), obj.get("responseId"))
        usage = obj.get("usageMetadata")
        if not isinstance(usage, dict):
            return
        self.found = True
        self.tokens.input = _int(usage.get("promptTokenCount"))
        self.tokens.output = _int(usage.get("candidatesTokenCount")) + _int(usage.get("thoughtsTokenCount"))

    def _meta(self, model, upstream_id) -> None:
        if isinstance(model, str) and model:
            self.model = model
        if isinstance(upstream_id, str) and upstream_id and not self.upstream_id:
            self.upstream_id = upstream_id

    # -- whole bodies --

    def feed_body(self, body: bytes) -> None:
        try:
            self.feed(json.loads(body))
        except (ValueError, UnicodeDecodeError):
            pass


@dataclass
class SseEvent:
    raw: bytes  # the event exactly as received, separator included
    data: object | None  # parsed JSON payload (None: no data / not JSON / [DONE])


class SseSplitter:
    """Cuts a byte stream into SSE events without changing a byte."""

    def __init__(self) -> None:
        self._buf = b""

    def push(self, chunk: bytes) -> list[SseEvent]:
        self._buf += chunk
        out = []
        while True:
            m = _SSE_SPLIT.search(self._buf)
            if not m:
                break
            raw, self._buf = self._buf[:m.end()], self._buf[m.end():]
            out.append(SseEvent(raw, _data(raw[:m.start()])))
        return out

    def flush(self) -> list[SseEvent]:
        raw, self._buf = self._buf, b""
        return [SseEvent(raw, _data(raw))] if raw else []


def _data(block: bytes) -> object | None:
    lines = [ln[5:].lstrip(b" ") for ln in re.split(rb"\r\n|\n|\r", block) if ln.startswith(b"data:")]
    if not lines:
        return None
    payload = b"\n".join(lines)
    if payload.strip() == b"[DONE]":
        return None
    try:
        return json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None


def is_usage_only_chunk(data: object) -> bool:
    """The extra final chunk OpenAI sends with stream_options.include_usage: no choices, only usage."""
    return isinstance(data, dict) and data.get("choices") == [] and isinstance(data.get("usage"), dict)
