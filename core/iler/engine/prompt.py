"""Prompt assembly (spec 5.1 step 5): product system prompt + bot profile + turn context + history."""

from datetime import datetime

from iler.llm.base import ChatMessage
from iler.settings_models import BotSettings, WeeklyHours

_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_DAY_KEYS = (("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"), ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс"))
_MEDIA_LABELS = {"image": "изображение", "video": "видео", "audio": "голосовое сообщение", "document": "документ",
                 "sticker": "стикер", "location": "геолокация", "contact": "контакт"}

PRODUCT_PROMPT = """Ты — ассистент заведения в WhatsApp. Отвечаешь клиентам от имени заведения.
Правила:
- Пиши коротко и по-человечески, как администратор в мессенджере. Без markdown-разметки и таблиц.
- Отвечай на языке клиента (русский или казахский; если клиент пишет на другом языке — на нём).
- Используй только сведения из профиля ниже и результаты инструментов. Не выдумывай цены, адреса, наличие мест и акции.
- Если нужен длинный справочник (цены, железо, акции, FAQ), вызови get_club_info.
- Бронь: собери дату, время, длительность и число мест, затем вызови create_booking_request и скажи клиенту,
  что заявка передана администратору и он подтвердит её. Не обещай, что бронь уже подтверждена.
- Если клиент недоволен, просит человека или вопрос вне твоей компетенции — вызови handover_to_operator.
- Токены вида [PHONE_1], [EMAIL_1], [CARD_1], [IIN_1] — скрытые данные клиента. Можешь повторять их как есть.
- Длинный ответ можно разбить на 2–3 сообщения маркером [SPLIT]."""


def _hours_text(hours: WeeklyHours) -> str:
    if hours.always_open:
        return "круглосуточно, без выходных"
    parts = []
    for key, label in _DAY_KEYS:
        d = getattr(hours, key)
        parts.append(f"{label}: выходной" if d.closed else f"{label}: {d.open}–{d.close}")
    return "; ".join(parts)


def build_system(bot: BotSettings, now_local: datetime, tz_name: str, client_name: str = "") -> str:
    sections = [PRODUCT_PROMPT, "", "## Профиль", f"Имя ассистента: {bot.name}"]
    if bot.persona.strip():
        sections.append(f"Персона: {bot.persona.strip()}")
    if bot.instructions.strip():
        sections += ["", "## Инструкции заведения", bot.instructions.strip()]
    if bot.services:
        sections += ["", "## Услуги и цены"]
        for s in bot.services:
            line = f"- {s.name}"
            if s.price:
                line += f": {s.price}"
            if s.note:
                line += f" ({s.note})"
            sections.append(line)
    if bot.faq:
        sections += ["", "## Частые вопросы"]
        sections += [f"- В: {f.q}\n  О: {f.a}" for f in bot.faq]
    if bot.address.strip():
        sections += ["", f"Адрес: {bot.address.strip()}"]
    sections.append(f"Часы работы: {_hours_text(bot.hours)}")
    if bot.greeting.strip():
        sections.append(f"Приветствие для нового клиента: {bot.greeting.strip()}")
    sections += [
        "",
        "## Контекст",
        f"Сейчас: {now_local.strftime('%d.%m.%Y %H:%M')} ({_WEEKDAYS[now_local.weekday()]}), часовой пояс {tz_name}",
    ]
    if client_name.strip():
        sections.append(f"Имя клиента в WhatsApp: {client_name.strip()}")
    return "\n".join(sections)


def message_text(text: str, media: dict | None) -> str:
    if media:
        label = _MEDIA_LABELS.get(str(media.get("type")), "вложение")
        return f"[{label}] {text}".strip()
    return text


def history_from_rows(rows) -> list[ChatMessage]:
    """DB messages -> chat history. Client -> user; bot/operator -> assistant; system notes are skipped."""
    out: list[ChatMessage] = []
    for r in rows:
        if r["author"] == "client":
            role = "user"
        elif r["author"] in ("bot", "operator"):
            role = "assistant"
        else:
            continue
        text = message_text(r["text"], r["media"])
        if text:
            out.append(ChatMessage(role=role, content=text))
    return out


def merge_consecutive(history: list[ChatMessage]) -> list[ChatMessage]:
    """Glue consecutive messages of the same role (a burst of client messages becomes one user turn)."""
    out: list[ChatMessage] = []
    for m in history:
        if m.role not in ("user", "assistant"):
            continue
        if out and out[-1].role == m.role:
            out[-1] = ChatMessage(role=m.role, content=f"{out[-1].content}\n{m.content}")
        else:
            out.append(ChatMessage(role=m.role, content=m.content))
    return out


def build_messages(bot: BotSettings, history: list[ChatMessage], now_local: datetime, tz_name: str,
                   client_name: str = "") -> list[ChatMessage]:
    recent = merge_consecutive(history[-bot.memory_messages:])
    while recent and recent[0].role != "user":  # providers expect the dialogue to start with the user
        recent.pop(0)
    return [ChatMessage(role="system", content=build_system(bot, now_local, tz_name, client_name)), *recent]
