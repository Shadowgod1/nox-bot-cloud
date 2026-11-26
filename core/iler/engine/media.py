"""Voice messages and photos the bot cannot read (no speech-to-text or vision in the reply path).

Without this the model sees only "[голосовое сообщение]" / "[изображение]" and guesses. Instead:
- voice message with no text: a fixed reply asking to write in text (``voice_mode="ask_text"``), or the
  conversation goes to the operator (``voice_mode="handover"``);
- photo / video (with or without a caption, e.g. "хочу такой дизайн"): "Спасибо! Передам фото мастеру" and,
  by default (``photo_mode="handover"``), the conversation goes to the operator who can see it.

The texts are settings; while the owner keeps the default text, a Kazakh-speaking client gets the Kazakh
version. Anything else (stickers, documents, voice with a text next to it) goes to the LLM as usual.
"""

from dataclasses import dataclass
from typing import Literal

from iler.engine import lang
from iler.settings_models import PHOTO_MESSAGE_DEFAULT, VOICE_MESSAGE_DEFAULT

VOICE_HANDOVER_DEFAULT = "Спасибо! Передам ваше голосовое администратору, он скоро ответит."
_KK = {
    "voice": "Әзірге дауыстық хабарламаны тыңдай алмаймын, мәтінмен жазып жіберіңізші 🙏",
    "voice_handover": "Рахмет! Дауыстық хабарламаңызды әкімшіге жіберемін, ол жақын арада жауап береді.",
    "photo": "Рахмет! Фотоны шеберге жіберемін, ол кеңес береді.",
}
_PHOTO_TYPES = {"image", "video"}


@dataclass
class MediaDecision:
    kind: Literal["voice", "photo"]
    reply: str
    handover: bool
    reason: str  # for the admin notification


def _type(media) -> str | None:
    if isinstance(media, dict):
        t = media.get("type")
        return str(t) if t else None
    if isinstance(media, str):
        return media or None
    return None


def _text(bot, field: str, default: str, kk_key: str, client_lang: str | None,
          ru_override: str | None = None) -> str:
    custom = (getattr(bot, field, "") or "").strip()
    if custom and custom != default:
        return custom  # the owner's own text, as written
    if client_lang == "kk":
        return _KK[kk_key]
    return ru_override or default


def decide(pending: list[tuple[str, object]], bot, earlier_texts: list[str] | None = None) -> MediaDecision | None:
    """``pending``: the client's (text, media) messages since the bot's / operator's last message, oldest
    first. ``earlier_texts``: older client texts, used only to guess the language. None = ask the LLM."""
    if not pending:
        return None
    types = [_type(m) for _, m in pending]
    texts = [(t or "").strip() for t, _ in pending]
    client_lang = lang.detect_recent([*(earlier_texts or []), *texts])
    if any(t in _PHOTO_TYPES for t in types):
        handover = getattr(bot, "photo_mode", "handover") == "handover"
        reply = _text(bot, "photo_message", PHOTO_MESSAGE_DEFAULT, "photo", client_lang)
        return MediaDecision("photo", reply, handover, "клиент прислал фото")
    if "audio" in types and not any(texts):
        handover = getattr(bot, "voice_mode", "ask_text") == "handover"
        if handover:
            reply = _text(bot, "voice_message", VOICE_MESSAGE_DEFAULT, "voice_handover", client_lang,
                          ru_override=VOICE_HANDOVER_DEFAULT)
        else:
            reply = _text(bot, "voice_message", VOICE_MESSAGE_DEFAULT, "voice", client_lang)
        return MediaDecision("voice", reply, handover, "клиент прислал голосовое сообщение")
    return None


def pending_from_rows(rows) -> tuple[list[tuple[str, object]], list[str]]:
    """Split DB message rows (oldest first) into the unanswered client messages and older client texts."""
    pending: list[tuple[str, object]] = []
    idx = len(rows)
    while idx > 0 and rows[idx - 1]["author"] == "client":
        idx -= 1
    for r in rows[idx:]:
        pending.append((r["text"] or "", r["media"]))
    earlier = [r["text"] for r in rows[:idx] if r["author"] == "client" and r["text"]]
    return pending, earlier
