"""Mock Anthropic / OpenAI / Gemini APIs for the local smoke run of the LLM gateway (no real keys, no network).

    python mock_upstream.py            # listens on 0.0.0.0:9000

Answers canned responses (plain JSON and SSE streams with usage) and checks that the gateway sent the
company key from MOCK_EXPECT_KEY (default "mock-company-key") in the provider's header; anything else
gets the provider-style 401. Only for portal/smoke/compose.smoke.yml.
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EXPECT = os.environ.get("MOCK_EXPECT_KEY", "mock-company-key")


def _sse(events: list[tuple[str | None, dict]]) -> bytes:
    out = b""
    for name, data in events:
        if name:
            out += f"event: {name}\n".encode()
        out += f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the smoke output short
        print(f"mock: {self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}", flush=True)

    def _send(self, status: int, body: bytes, ctype: str = "application/json", stream: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("request-id", "req_mock_1")
        if stream:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for part in body.split(b"\n\n"):
                if not part:
                    continue
                chunk = part + b"\n\n"
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
                time.sleep(0.05)
            self.wfile.write(b"0\r\n\r\n")
        else:
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        path = self.path.split("?")[0]
        stream = bool(body.get("stream")) or ":streamGenerateContent" in path
        if path == "/v1/messages":
            if self.headers.get("x-api-key") != EXPECT or self.headers.get("authorization"):
                return self._send(401, b'{"type":"error","error":{"type":"authentication_error"}}')
            usage = {"input_tokens": 1000, "output_tokens": 500, "cache_creation_input_tokens": 0,
                     "cache_read_input_tokens": 200}
            if stream:
                return self._send(200, _sse([
                    ("message_start", {"type": "message_start", "message": {
                        "id": "msg_mock_s", "type": "message", "model": body.get("model", "claude-haiku-5-5"),
                        "usage": {**usage, "output_tokens": 1}}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "text_delta", "text": "Здравствуйте"}}),
                    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                       "usage": {"output_tokens": 500}}),
                    ("message_stop", {"type": "message_stop"}),
                ]), "text/event-stream", stream=True)
            return self._send(200, json.dumps({
                "id": "msg_mock", "type": "message", "role": "assistant", "model": body.get("model", "claude-haiku-5-5"),
                "content": [{"type": "text", "text": "Здравствуйте"}], "stop_reason": "end_turn", "usage": usage,
            }, ensure_ascii=False).encode())
        if path == "/v1/chat/completions":
            if self.headers.get("authorization") != f"Bearer {EXPECT}":
                return self._send(401, b'{"error":{"type":"invalid_request_error","code":"invalid_api_key"}}')
            usage = {"prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1000}
            base = {"id": "chatcmpl-mock", "object": "chat.completion.chunk", "model": body.get("model", "gpt-5.4-mini")}
            if stream:
                events = [(None, {**base, "choices": [{"index": 0, "delta": {"content": "Да"}, "finish_reason": None}]}),
                          (None, {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})]
                if (body.get("stream_options") or {}).get("include_usage"):
                    events.append((None, {**base, "choices": [], "usage": usage}))
                return self._send(200, _sse(events) + b"data: [DONE]\n\n", "text/event-stream", stream=True)
            return self._send(200, json.dumps({**base, "object": "chat.completion", "usage": usage, "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "Да"}, "finish_reason": "stop"}]}).encode())
        if path.startswith("/v1beta/models/"):
            if self.headers.get("x-goog-api-key") != EXPECT:
                return self._send(403, b'{"error":{"code":403,"status":"PERMISSION_DENIED"}}')
            chunk = {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
                     "usageMetadata": {"promptTokenCount": 600, "candidatesTokenCount": 100, "thoughtsTokenCount": 50},
                     "modelVersion": "gemini-3.8-flash", "responseId": "gem-mock"}
            if stream:
                return self._send(200, _sse([(None, chunk)]), "text/event-stream", stream=True)
            return self._send(200, json.dumps(chunk).encode())
        return self._send(404, b'{"error":"not found"}')


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
