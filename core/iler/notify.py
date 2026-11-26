"""Lead notifications outside WhatsApp: Telegram through the owner's own bot.

- The bot token is a secret (encrypted at rest, write-only in the API). It is part of the Bot API URL, so
  it must never reach a log: httpx request logging is turned down for this module's client and every error
  is reported without the URL.
- Each notification has a deterministic key (`notification_log`), so a repeated call never sends twice.
- Nothing here raises to the caller except `TelegramError` from the explicit check/test helpers.
"""

import logging
import re

import httpx

from iler.db.pool import get_pool
from iler.settings_models import NotifySettings

log = logging.getLogger("iler.notify")

TELEGRAM_API = "https://api.telegram.org"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
TEXT_MAX = 4000
_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{20,100}$")

# httpx logs every request URL at INFO; for Telegram the URL contains the token.
logging.getLogger("httpx").setLevel(logging.WARNING)

_transport: httpx.AsyncBaseTransport | None = None  # tests inject a MockTransport


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


class TelegramError(Exception):
    """A Telegram call failed; the message is safe to show (never contains the token)."""


def token_looks_valid(token: str) -> bool:
    return bool(_TOKEN_RE.match(token or ""))


def _token(notify: NotifySettings) -> str:
    return notify.telegram_bot_token.get_secret_value() if notify.telegram_bot_token else ""


def telegram_configured(notify: NotifySettings) -> bool:
    return bool(_token(notify)) and bool(notify.telegram_chat_ids)


async def _call(token: str, method: str, payload: dict | None = None) -> dict:
    if not token_looks_valid(token):
        raise TelegramError("токен бота выглядит неверно: он вида 123456789:AA…")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport) as client:
            r = await client.post(f"{TELEGRAM_API}/bot{token}/{method}", json=payload or {})
    except httpx.HTTPError as e:
        raise TelegramError(f"Telegram недоступен ({type(e).__name__})") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code == 401 or r.status_code == 404:
        raise TelegramError("Telegram не принял токен бота — проверьте его в @BotFather")
    if r.status_code >= 400 or not data.get("ok"):
        desc = str(data.get("description") or f"HTTP {r.status_code}")
        if "chat not found" in desc.lower():
            raise TelegramError("чат не найден — напишите вашему боту /start и выберите чат заново")
        if "blocked" in desc.lower():
            raise TelegramError("бот заблокирован в этом чате — разблокируйте его в Telegram")
        raise TelegramError(f"Telegram ответил ошибкой: {desc[:200].replace(token, '***')}")
    return data


async def telegram_send(token: str, chat_id: str, text: str) -> None:
    await _call(token, "sendMessage", {"chat_id": chat_id, "text": text[:TEXT_MAX],
                                       "disable_web_page_preview": True})


async def telegram_chats(token: str) -> list[dict]:
    """Chats that wrote to the owner's bot recently (getUpdates): [{"id": "123", "title": "Айгерим"}].
    The owner writes /start to the bot, then picks the chat here instead of looking up a numeric id."""
    data = await _call(token, "getUpdates", {"allowed_updates": ["message", "my_chat_member"]})
    chats: dict[str, str] = {}
    for upd in data.get("result") or []:
        if not isinstance(upd, dict):
            continue
        for key in ("message", "my_chat_member", "channel_post"):
            chat = (upd.get(key) or {}).get("chat") if isinstance(upd.get(key), dict) else None
            if not isinstance(chat, dict) or chat.get("id") is None:
                continue
            title = chat.get("title") or " ".join(
                x for x in (chat.get("first_name"), chat.get("last_name")) if x
            ) or (f"@{chat['username']}" if chat.get("username") else str(chat["id"]))
            chats[str(chat["id"])] = str(title)[:80]
    return [{"id": cid, "title": title} for cid, title in chats.items()]


async def conversation_link(conversation_id: int | None) -> str:
    """Link to the conversation in the admin panel: the saved admin address, else the public URL; '' when
    neither is known (a LAN install where the owner did not save the notification settings yet)."""
    if conversation_id is None:
        return ""
    try:
        from iler import settings_store
        from iler.config import get_settings

        notify = await settings_store.get("notify", NotifySettings)
        base = notify.admin_url or get_settings().public_url.strip().rstrip("/")
    except Exception:  # noqa: BLE001
        return ""
    return f"{base}/conversations/{conversation_id}" if base.lower().startswith(("http://", "https://")) else ""


async def _claim(key: str) -> bool:
    return bool(await get_pool().fetchval(
        "INSERT INTO notification_log(key, channel, status) VALUES ($1, 'telegram', 'sending')"
        " ON CONFLICT (key) DO NOTHING RETURNING 1", key,
    ))


async def notify_telegram(notify: NotifySettings, text: str, *, key: str) -> list[str]:
    """Send `text` to every configured Telegram chat once per key. Returns statuses; never raises."""
    if not telegram_configured(notify):
        return []
    token = _token(notify)
    statuses: list[str] = []
    for chat_id in notify.telegram_chat_ids:
        k = f"tg:{key}:{chat_id}"
        try:
            if not await _claim(k):
                statuses.append("duplicate")
                continue
            try:
                await telegram_send(token, chat_id, text)
                status, error = "sent", None
            except TelegramError as e:
                status, error = "failed", str(e)
                log.warning("Telegram notification to chat %s failed: %s", chat_id, e)
            await get_pool().execute(
                "UPDATE notification_log SET status = $2, error = $3 WHERE key = $1", k, status, error,
            )
            statuses.append(status)
        except Exception:  # noqa: BLE001 - a notification must not break the caller
            log.exception("Telegram notification failed")
            statuses.append("failed")
    return statuses
