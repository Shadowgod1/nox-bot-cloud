"""LLM gateway: /llm/<provider>/<path> -> the provider's API with the company key, metered in coins.

  /llm/anthropic/... -> https://api.anthropic.com/...
  /llm/openai/...    -> https://api.openai.com/...
  /llm/gemini/...    -> https://generativelanguage.googleapis.com/...
  GET /llm/v1/balance -> {"coins_left", "monthly_limit", "period_end", "status", "providers", "spent", "trial_end"}
    providers: the providers this gateway has a company key for (the bot offers only those)

The client sends its virtual key (nox_vk_...) in x-api-key, Authorization: Bearer or x-goog-api-key (any
of them works for any provider); ?key= in the URL is refused with 400 so keys never land in access logs.
The gateway strips it and sends the company key in the provider's own header. Method, path, query, body
and the relevant headers pass through; the response status and body come back unchanged and streams
(SSE) are forwarded event by event without buffering.

Only generation, token counting, embeddings and model-list endpoints are proxied (ALLOWED): anything else
(files, batches, fine-tuning, images, audio, ...) would spend money the gateway cannot meter.

Errors of the gateway itself: {"error": {"type": ..., "message": ...}}.
  400 key_in_url | 401 invalid_key | 401 key_revoked | 402 insufficient_coins | 404 endpoint_not_allowed
  413 request_too_large | 429 rate_limited | 502/504 upstream_unavailable | 503 provider_not_configured

OpenAI streaming usage: when a Chat Completions stream does not ask for stream_options.include_usage the
gateway adds it (otherwise the stream carries no usage) and drops the extra final usage-only chunk
(choices: []) before it reaches the client, because clients that did not ask for it may index choices[0].
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from decimal import Decimal

import anyio
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from nox_portal import alerts, prices, ratelimit, wallet
from nox_portal.config import get_settings
from nox_portal.db import get_pool
from nox_portal.errors import ApiError
from nox_portal.metering import Meter, SseSplitter, is_usage_only_chunk
from nox_portal.prices import Tokens

log = logging.getLogger("nox_portal.gateway")
router = APIRouter(tags=["llm"])

_MODEL_ID = r"[A-Za-z0-9._:@-]+"
ALLOWED: dict[str, re.Pattern] = {
    "anthropic": re.compile(rf"^v1/(messages|messages/count_tokens|models|models/{_MODEL_ID})$"),
    "openai": re.compile(rf"^v1/(chat/completions|responses|embeddings|models|models/{_MODEL_ID})$"),
    "gemini": re.compile(
        r"^(v1|v1beta)/models(/[A-Za-z0-9._-]+"
        r"(:(generateContent|streamGenerateContent|countTokens|embedContent|batchEmbedContents))?)?$"
    ),
}
# endpoints that cost money: a successful answer without usage is still charged (estimate), never 0
METERED: dict[str, re.Pattern] = {
    "anthropic": re.compile(r"^v1/messages$"),
    "openai": re.compile(r"^v1/(chat/completions|responses|embeddings)$"),
    "gemini": re.compile(r":(generateContent|streamGenerateContent|embedContent|batchEmbedContents)$"),
}
_GEMINI_MODEL = re.compile(r"models/([A-Za-z0-9._-]+)")

# request headers passed upstream (everything else, cookies and all auth headers included, is dropped)
_FORWARD_REQUEST = {"content-type", "accept", "user-agent", "anthropic-version", "anthropic-beta", "openai-beta",
                    "idempotency-key", "x-goog-api-client"}
# response headers passed back (provider org ids, rate-limit details of the company account etc. are not)
_FORWARD_RESPONSE = {"content-type", "cache-control", "retry-after", "retry-after-ms", "x-should-retry",
                     "request-id", "x-request-id", "openai-processing-ms", "openai-version"}
_AUTH_HEADER = {"anthropic": "x-api-key", "openai": "authorization", "gemini": "x-goog-api-key"}
DEFAULT_MAX_TOKENS = 8192
PARSE_LIMIT = 32 * 1024 * 1024

client: httpx.AsyncClient | None = None
_settle_tasks: set[asyncio.Task] = set()
_hits: dict[int, deque[float]] = defaultdict(deque)


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(connect=10.0, read=float(s.gateway_timeout_seconds), write=60.0, pool=10.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        follow_redirects=False,
    )


def http() -> httpx.AsyncClient:
    global client
    if client is None:
        client = make_client()
    return client


def error(status: int, kind: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"type": kind, "message": message}}, status_code=status, headers=headers)


def _upstream(provider: str) -> tuple[str, str]:
    s = get_settings()
    return (getattr(s, f"upstream_{provider}_base_url").rstrip("/"), getattr(s, f"upstream_{provider}_key").strip())


def virtual_key(request: Request) -> str | None:
    for name in ("x-api-key", "x-goog-api-key"):
        value = request.headers.get(name, "").strip()
        if value:
            return value
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip() or None
    return None


def rate_limited(key_id: int) -> int | None:
    """In-process sliding window per key (the api runs one process). Returns Retry-After seconds or None."""
    limit = get_settings().gateway_rate_per_minute
    if limit <= 0:
        return None
    now = time.monotonic()
    q = _hits[key_id]
    while q and q[0] <= now - 60:
        q.popleft()
    if len(q) >= limit:
        return max(int(60 - (now - q[0])) + 1, 1)
    q.append(now)
    return None


async def _authenticate(request: Request) -> tuple[wallet.KeyOwner | None, JSONResponse | None]:
    if "key" in request.query_params:
        return None, error(400, "key_in_url", "Передавайте ключ в заголовке (x-goog-api-key, x-api-key или "
                                              "Authorization: Bearer), а не в адресе запроса")
    key = virtual_key(request)
    owner = await wallet.lookup(get_pool(), key) if key else None
    if owner is None:
        try:
            await ratelimit.hit("llm-badkey:ip", ratelimit.client_ip(request))
        except ApiError:
            return None, error(429, "rate_limited", "Слишком много запросов с неверным ключом", {"Retry-After": "600"})
        return None, error(401, "invalid_key", "Неверный или отсутствующий ключ Nox (nox_vk_...)")
    retry = rate_limited(owner.key_id)
    if retry is not None:
        return None, error(429, "rate_limited", "Слишком много запросов к шлюзу, повторите позже",
                           {"Retry-After": str(retry)})
    return owner, None


@router.get("/llm/v1/balance")
async def get_balance(request: Request):
    owner, err = await _authenticate(request)
    if err is not None:
        return err
    assert owner is not None
    bal = await wallet.balance(get_pool(), owner.account_id)
    if owner.key_status != "active":
        status = "revoked"
    elif owner.account_status not in wallet.LIVE_STATUSES:
        status = "suspended"
    else:
        status = "active"
    s = get_settings()
    providers = [p for p in ("anthropic", "openai", "gemini") if getattr(s, f"upstream_{p}_key").strip()]
    # spent: the exact ledger spend of the period (coins_left is whole coins, so limit - coins_left overstates it);
    # trial_end: a Free account stops at this moment, before period_end
    return {"coins_left": bal.coins_left, "monthly_limit": bal.monthly_limit,
            "period_end": bal.period_end.isoformat() if bal.period_end else None, "status": status,
            "providers": providers, "spent": round(float(bal.spent), 2),
            "trial_end": bal.trial_end.isoformat() if bal.trial_end else None}


async def _read_body(request: Request, cap: int) -> bytes | None:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > cap:
        return None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _model_of(provider: str, path: str, payload: dict | None) -> str:
    if provider == "gemini":
        m = _GEMINI_MODEL.search(path)
        return m.group(1) if m else ""
    return str((payload or {}).get("model") or "")


def _max_tokens(provider: str, payload: dict | None) -> int:
    p = payload or {}
    if provider == "gemini":
        value = (p.get("generationConfig") or {}).get("maxOutputTokens") if isinstance(p.get("generationConfig"), dict) else None
    else:
        value = p.get("max_tokens") or p.get("max_completion_tokens") or p.get("max_output_tokens")
    try:
        return max(int(value), 1) if value else DEFAULT_MAX_TOKENS
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS


def estimate(provider: str, model: str, body: bytes, payload: dict | None) -> tuple[Decimal, Decimal]:
    """(reservation, input-only estimate). Input tokens are bounded by body bytes / 3; output by max_tokens."""
    price, _ = prices.price_for(provider, model)
    tin = len(body) // 3 + 1
    input_only = prices.coins(price, Tokens(input=tin))
    if provider == "anthropic":  # the prompt might be written to the cache (more expensive than input)
        input_only = max(input_only, prices.coins(price, Tokens(cache_write=tin)))
    return input_only + prices.coins(price, Tokens(output=_max_tokens(provider, payload))), input_only


def _forward_headers(request: Request, provider: str, upstream_key: str) -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARD_REQUEST or k.lower().startswith("x-stainless-")}
    headers["accept-encoding"] = "identity"  # usage is parsed from the bytes we forward
    headers[_AUTH_HEADER[provider]] = f"Bearer {upstream_key}" if provider == "openai" else upstream_key
    return headers


def _response_headers(upstream: httpx.Response, request_id: str) -> dict[str, str]:
    out = {k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD_RESPONSE}
    out["x-nox-request-id"] = request_id
    out["x-accel-buffering"] = "no"
    return out


@router.api_route("/llm/{provider}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(provider: str, path: str, request: Request):
    if provider not in ALLOWED:
        return error(404, "endpoint_not_allowed", f"Неизвестный провайдер {provider!r}: anthropic, openai или gemini")
    owner, err = await _authenticate(request)
    if err is not None:
        return err
    assert owner is not None
    if not owner.live:
        message = ("Ключ отозван" if owner.key_status != "active"
                   else "Обслуживание приостановлено: свяжитесь с Nox service")
        return error(401, "key_revoked", message)
    if not ALLOWED[provider].match(path):
        return error(404, "endpoint_not_allowed", f"Этот метод API не доступен через шлюз Nox: /{path}")
    base, upstream_key = _upstream(provider)
    if not upstream_key:
        return error(503, "provider_not_configured", f"Провайдер {provider} не подключён в шлюзе Nox")
    s = get_settings()
    body = await _read_body(request, s.gateway_max_body_bytes)
    if body is None:
        return error(413, "request_too_large", f"Запрос больше {s.gateway_max_body_bytes // (1024 * 1024)} МБ")

    payload = None
    if body:
        try:
            parsed = json.loads(body)
            payload = parsed if isinstance(parsed, dict) else None
        except (ValueError, UnicodeDecodeError):
            payload = None
    injected = False
    if (provider == "openai" and path == "v1/chat/completions" and payload is not None and payload.get("stream") is True
            and not (isinstance(payload.get("stream_options"), dict) and payload["stream_options"].get("include_usage"))):
        payload["stream_options"] = {**(payload.get("stream_options") or {}), "include_usage": True}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        injected = True

    metered = bool(METERED[provider].search(path))
    model = _model_of(provider, path, payload)
    reservation_id = None
    input_estimate = Decimal(0)
    pool = get_pool()
    if metered:
        reserve, input_estimate = estimate(provider, model, body, payload)
        reservation_id = await wallet.admit(pool, owner.account_id, reserve)
        if reservation_id is None:
            return error(402, "insufficient_coins", "Коины закончились")

    request_id = "gw_" + uuid.uuid4().hex
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "key"]
    upstream_req = http().build_request(request.method, f"{base}/{path}", params=params, content=body or None,
                                        headers=_forward_headers(request, provider, upstream_key))
    try:
        upstream = await http().send(upstream_req, stream=True)
    except httpx.TimeoutException:
        await wallet.release(pool, reservation_id)
        alerts.record(provider, None)
        return error(504, "upstream_unavailable", f"{provider} не ответил вовремя")
    except httpx.HTTPError as exc:
        await wallet.release(pool, reservation_id)
        alerts.record(provider, None)
        log.warning("upstream %s failed: %s", provider, type(exc).__name__)
        return error(502, "upstream_unavailable", f"Нет связи с {provider}")

    meter = Meter(provider)
    ctx = _Ctx(owner, provider, path, model, metered, reservation_id, input_estimate, request_id, upstream.status_code)
    headers = _response_headers(upstream, request_id)
    is_sse = upstream.headers.get("content-type", "").lower().startswith("text/event-stream")
    return StreamingResponse(_relay(upstream, meter, ctx, is_sse, injected), status_code=upstream.status_code,
                             headers=headers, media_type=None)


class _Ctx:
    def __init__(self, owner, provider, path, model, metered, reservation_id, input_estimate, request_id, status):
        self.owner, self.provider, self.path, self.model = owner, provider, path, model
        self.metered, self.reservation_id, self.input_estimate = metered, reservation_id, input_estimate
        self.request_id, self.status = request_id, status
        self.upstream_request_id: str | None = None


async def _relay(upstream: httpx.Response, meter: Meter, ctx: _Ctx, is_sse: bool, injected: bool):
    ctx.upstream_request_id = upstream.headers.get("request-id") or upstream.headers.get("x-request-id")
    copy = bytearray()
    splitter = SseSplitter() if is_sse else None
    failed = None
    try:
        async for chunk in upstream.aiter_bytes():
            if splitter is None:
                if len(copy) < PARSE_LIMIT:
                    copy += chunk
                yield chunk
                continue
            out = bytearray()
            for event in splitter.push(chunk):
                meter.feed(event.data)
                if injected and is_usage_only_chunk(event.data):
                    continue
                out += event.raw
            if out:
                yield bytes(out)
        if splitter is not None:
            for event in splitter.flush():
                meter.feed(event.data)
                if not (injected and is_usage_only_chunk(event.data)):
                    yield event.raw
    except httpx.HTTPError as exc:
        failed = type(exc).__name__
        log.warning("upstream %s stream broke: %s", ctx.provider, failed)
    finally:
        # no await before the settle task exists: on a client disconnect every await here is cancelled
        if splitter is None and copy:
            meter.feed_body(bytes(copy))
        if ctx.status >= 400:
            alerts.record(ctx.provider, ctx.status, bytes(copy[:4000]))
        elif failed:
            alerts.record(ctx.provider, None)
        task = asyncio.create_task(_settle(meter, ctx))
        _settle_tasks.add(task)
        task.add_done_callback(_settle_tasks.discard)
        with anyio.CancelScope(shield=True):
            await upstream.aclose()


def charge_for(meter: Meter, ctx: _Ctx) -> wallet.Charge | None:
    model = meter.model or ctx.model or "unknown"
    if meter.found and meter.tokens.total > 0:
        price, priced = prices.price_for(ctx.provider, model)
        coins = prices.coins(price, meter.tokens)
        tokens = meter.tokens
    elif ctx.metered and ctx.status < 400:
        # a successful (or cut-off) answer without usage: charge the input estimate, never 0
        log.warning("no usage in a %s %s answer (request %s): charging the input estimate",
                    ctx.provider, ctx.path, ctx.request_id)
        coins, priced, tokens = ctx.input_estimate, False, Tokens()
    else:
        return None
    return wallet.Charge(
        account_id=ctx.owner.account_id, key_id=ctx.owner.key_id, provider=ctx.provider, model=model,
        endpoint=ctx.path, input_tokens=tokens.input, output_tokens=tokens.output,
        cache_write_tokens=tokens.cache_write, cache_read_tokens=tokens.cache_read, coins=coins, priced=priced,
        status=ctx.status, request_id=ctx.request_id, upstream_request_id=meter.upstream_id or ctx.upstream_request_id,
    )


async def _settle(meter: Meter, ctx: _Ctx) -> None:
    try:
        await wallet.settle(get_pool(), ctx.reservation_id, charge_for(meter, ctx))
    except Exception:  # noqa: BLE001 - never lose the reason; the reservation expires by itself
        log.exception("settling request %s failed", ctx.request_id)


async def drain() -> None:
    """Wait for pending settlements (tests, shutdown)."""
    while _settle_tasks:
        await asyncio.gather(*list(_settle_tasks), return_exceptions=True)


def reset_limits() -> None:
    _hits.clear()

