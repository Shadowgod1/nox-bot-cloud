"""Parsing of Evolution v2 webhook payloads."""

import re
from datetime import UTC, datetime

from iler.whatsapp.base import IncomingMessage

# Wrappers whose inner `message` holds the real content.
_WRAPPERS = ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension",
             "documentWithCaptionMessage", "editedMessage")
_MEDIA = {
    "imageMessage": "image",
    "videoMessage": "video",
    "audioMessage": "audio",
    "documentMessage": "document",
    "stickerMessage": "sticker",
    "locationMessage": "location",
    "liveLocationMessage": "location",
    "contactMessage": "contact",
    "contactsArrayMessage": "contact",
}
_IGNORED_SUFFIXES = ("@broadcast", "@newsletter")


def event_name(payload: dict) -> str:
    """Normalize `messages.upsert` / `MESSAGES_UPSERT` to `MESSAGES_UPSERT`."""
    return str(payload.get("event") or "").upper().replace(".", "_")


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", (value or "").split("@", 1)[0].split(":", 1)[0])


def _unwrap(message: dict) -> dict:
    for _ in range(4):
        for w in _WRAPPERS:
            inner = message.get(w)
            if isinstance(inner, dict) and isinstance(inner.get("message"), dict):
                message = inner["message"]
                break
        else:
            return message
    return message


def _text_and_media(message: dict) -> tuple[str, dict | None]:
    if isinstance(message.get("conversation"), str):
        return message["conversation"], None
    ext = message.get("extendedTextMessage")
    if isinstance(ext, dict) and ext.get("text"):
        return str(ext["text"]), None
    for key, kind in _MEDIA.items():
        part = message.get(key)
        if isinstance(part, dict):
            media = {"type": kind}
            for f in ("mimetype", "fileName", "seconds", "degreesLatitude", "degreesLongitude", "displayName"):
                if f in part:
                    media[f] = part[f]
            return str(part.get("caption") or ""), media
    for key, field in (("buttonsResponseMessage", "selectedDisplayText"),
                       ("templateButtonReplyMessage", "selectedDisplayText"),
                       ("listResponseMessage", "title")):
        part = message.get(key)
        if isinstance(part, dict) and part.get(field):
            return str(part[field]), None
    return "", None


def message_timestamp(value) -> datetime | None:
    """`messageTimestamp` as an aware datetime: seconds as int/str, or a protobuf Long {low, high}."""
    if isinstance(value, dict):
        try:
            value = (int(value.get("high") or 0) << 32) + (int(value.get("low") or 0) & 0xFFFFFFFF)
        except (TypeError, ValueError):
            return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds > 10**11:  # milliseconds
        seconds //= 1000
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def history_contacts(payload: dict) -> list[tuple[str, str]]:
    """(phone, jid) of private chats in a history-sync webhook (MESSAGES_SET / CHATS_SET)."""
    data = payload.get("data")
    if isinstance(data, dict):
        data = data.get("messages") or data.get("chats") or [data]
    if not isinstance(data, list):
        return []
    out: list[tuple[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        key = item.get("key") if isinstance(item.get("key"), dict) else {}
        jid = str(key.get("remoteJid") or item.get("remoteJid") or item.get("id") or "")
        if not jid or jid.endswith(("@g.us", "@broadcast", "@newsletter")):
            continue
        alt = key.get("remoteJidAlt") or key.get("senderPn")
        if jid.endswith("@lid") and alt:
            jid = alt if "@" in str(alt) else f"{_digits(alt)}@s.whatsapp.net"
        phone = "" if jid.endswith("@lid") else _digits(jid)
        out.append((phone, jid))
    return out


def parse_upsert(payload: dict, received_at: datetime | None = None) -> IncomingMessage | None:
    """Turn a MESSAGES_UPSERT webhook into an IncomingMessage.

    Returns None for other events, status/broadcast/newsletter chats and service messages
    (reactions, revokes, key distribution). Group messages are returned with `is_group=True`;
    filtering them is the dispatcher's job (the admin group must get through).
    `ts` is our own receive time: Evolution clocks may drift (spec section 11.8).
    """
    if event_name(payload) != "MESSAGES_UPSERT":
        return None
    data = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None
    key = data.get("key") or {}
    evo_id = key.get("id")
    remote = str(key.get("remoteJid") or "")
    if not evo_id or not remote or remote.endswith(_IGNORED_SUFFIXES):
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    text, media = _text_and_media(_unwrap(message))
    if not text and media is None:
        return None  # reaction, protocol message, key distribution, poll update, ...

    is_group = remote.endswith("@g.us")
    jid = remote
    if is_group:
        # participantAlt carries the phone JID when the participant is addressed by LID
        sender = key.get("participantAlt") or key.get("participant") or data.get("participant") or ""
        phone = "" if str(sender).endswith("@lid") else _digits(sender)
    else:
        if remote.endswith("@lid"):
            alt = key.get("remoteJidAlt") or key.get("senderPn")
            if alt:
                jid = alt if "@" in str(alt) else f"{_digits(alt)}@s.whatsapp.net"
        phone = "" if jid.endswith("@lid") else _digits(jid)

    return IncomingMessage(
        evo_id=str(evo_id),
        jid=jid,
        phone=phone,
        push_name=str(data.get("pushName") or ""),
        text=text,
        from_me=bool(key.get("fromMe")),
        is_group=is_group,
        media=media,
        ts=received_at or datetime.now(UTC),
        sent_at=message_timestamp(data.get("messageTimestamp")),
    )


def message_payloads(payload: dict) -> list[dict]:
    """Split a MESSAGES_UPSERT whose `data` may be a list into single-message payloads."""
    data = payload.get("data")
    items = data if isinstance(data, list) else [data]
    return [{**payload, "data": d} for d in items if isinstance(d, dict)]
