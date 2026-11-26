"""Staff alerts about the company's provider accounts: e-mail to NOX_STAFF_NOTIFY and optional Telegram.

  auth    upstream 401/403: the company key in NOX_UPSTREAM_<PROVIDER>_KEY is wrong or disabled (at once)
  credit  upstream 402 or an "insufficient credit / quota" error: top up the provider console (at once)
  errors  5xx / timeouts from one provider: NOX_ALERT_ERROR_THRESHOLD within NOX_ALERT_WINDOW_SECONDS

The same provider + kind is not repeated within NOX_ALERT_COOLDOWN_SECONDS. The Telegram bot token is
part of the request URL: it is never logged (httpx request logging is silenced in main).
"""

import asyncio
import logging
import time
from collections import defaultdict, deque

import httpx

from nox_portal import mailer
from nox_portal.config import get_settings

log = logging.getLogger("nox_portal.alerts")

CREDIT_MARKERS = ("credit balance", "insufficient_quota", "billing", "exceeded your current quota",
                  "insufficient credit", "payment required")

_errors: dict[str, deque[float]] = defaultdict(deque)
_last_sent: dict[tuple[str, str], float] = {}
_tasks: set[asyncio.Task] = set()
# test hook / shared client: set by the gateway (main lifespan)
client: httpx.AsyncClient | None = None


def reset() -> None:
    _errors.clear()
    _last_sent.clear()


def classify(status: int | None, body: bytes | None) -> str | None:
    """Kind of a failed upstream answer: auth | credit | errors | None (not an alert)."""
    text = (body or b"")[:4000].decode("utf-8", "replace").lower()
    if status == 402 or (status in (400, 403, 429) and any(m in text for m in CREDIT_MARKERS)):
        return "credit"
    if status in (401, 403):
        return "auth"
    if status is None or status >= 500:
        return "errors"
    return None


def record(provider: str, status: int | None, body: bytes | None = None) -> None:
    """Called for every failed upstream answer (status None = timeout / connection error)."""
    kind = classify(status, body)
    if kind is None:
        return
    s = get_settings()
    if kind == "errors":
        now = time.monotonic()
        q = _errors[provider]
        q.append(now)
        while q and q[0] < now - s.alert_window_seconds:
            q.popleft()
        if len(q) < s.alert_error_threshold:
            return
        detail = f"{len(q)} ошибок 5xx/таймаутов за {s.alert_window_seconds // 60} мин (последняя: {status or 'таймаут'})"
    elif kind == "auth":
        detail = f"провайдер отклонил ключ компании (HTTP {status}): проверьте NOX_UPSTREAM_{provider.upper()}_KEY"
    else:
        detail = f"у провайдера закончился баланс или квота (HTTP {status}): пополните консоль {provider}"
    key = (provider, kind)
    now = time.monotonic()
    if now - _last_sent.get(key, -1e18) < s.alert_cooldown_seconds:
        return
    _last_sent[key] = now
    task = asyncio.create_task(send(f"Nox LLM-шлюз: {provider} — {kind}", detail))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def telegram(text: str) -> bool:
    s = get_settings()
    if not s.alert_telegram_bot_token or not s.alert_telegram_chat_id:
        return False
    url = f"{s.alert_telegram_api.rstrip('/')}/bot{s.alert_telegram_bot_token}/sendMessage"
    try:
        http = client or httpx.AsyncClient(timeout=15)
        r = await http.post(url, json={"chat_id": s.alert_telegram_chat_id, "text": text[:4000],
                                       "disable_web_page_preview": True}, timeout=15)
        if client is None:
            await http.aclose()
        if r.status_code != 200:
            log.error("telegram alert failed: HTTP %s", r.status_code)
            return False
        return True
    except httpx.HTTPError as exc:
        # str(exc) may contain the URL with the token: only the type is logged
        log.error("telegram alert failed: %s", type(exc).__name__)
        return False


async def send(subject: str, text: str) -> None:
    for to in mailer.staff_recipients():
        await mailer.send_quiet(to, subject, text)
    await telegram(f"{subject}\n{text}")
    log.warning("alert sent: %s — %s", subject, text)


async def drain() -> None:
    """Wait for pending alert sends (tests)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
