"""Typed settings stored in the `settings` table (one row per key)."""

import re
from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, SecretStr, field_validator

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")

TEMPLATE_VALUE_MAX = 4000
TEMPLATE_VALUES_MAX_KEYS = 50
PROMPT_MAX = 20000


class DayHours(BaseModel):
    open: str = "00:00"   # HH:MM
    close: str = "24:00"  # HH:MM, "24:00" = end of day
    closed: bool = False

    @field_validator("open", "close")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not isinstance(v, str) or not _HHMM.match(v):
            raise ValueError("time must be HH:MM (00:00–23:59) or 24:00")
        return v


class WeeklyHours(BaseModel):
    always_open: bool = True
    mon: DayHours = DayHours()
    tue: DayHours = DayHours()
    wed: DayHours = DayHours()
    thu: DayHours = DayHours()
    fri: DayHours = DayHours()
    sat: DayHours = DayHours()
    sun: DayHours = DayHours()


EXCLUDE_PHONES_MAX = 2000


def normalize_phone_digits(value: str | None) -> str:
    """Digits only; a KZ/RU local number 8XXXXXXXXXX (11 digits) becomes 7XXXXXXXXXX."""
    d = re.sub(r"\D", "", (value or "").split("@", 1)[0])
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    return d


NO_COINS_MESSAGE_DEFAULT = "Спасибо за сообщение! Сейчас подключится менеджер и ответит вам."


# onboarding/knowledge: the owner's price list, staff with working days, and what the bot does with
# voice messages and photos it cannot read. Used by engine.prompt (knowledge sections) and engine.media.
SERVICES_MAX = 300
STAFF_MAX = 50
VOICE_MESSAGE_DEFAULT = "Пока не могу прослушать голосовое, напишите, пожалуйста, текстом 🙏"
PHOTO_MESSAGE_DEFAULT = "Спасибо! Передам фото мастеру, он подскажет."


class ServiceItem(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    # free text as the owner writes it: "5000 ₸", "от 5000 ₸", "5000–7000 ₸"; "от … ₸" or "" = not filled yet
    price: str = Field("", max_length=60)
    duration_min: int | None = Field(None, ge=1, le=1440)


class StaffDay(BaseModel):
    works: bool = False
    open: str = "10:00"
    close: str = "19:00"

    @field_validator("open", "close")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not isinstance(v, str) or not _HHMM.match(v):
            raise ValueError("time must be HH:MM (00:00–23:59) or 24:00")
        return v


class StaffMember(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    role: str = Field("", max_length=120)  # "мастер маникюра", "терапевт-стоматолог"
    services: list[str] = Field(default_factory=list, max_length=SERVICES_MAX)  # ServiceItem.name values
    mon: StaffDay = StaffDay()
    tue: StaffDay = StaffDay()
    wed: StaffDay = StaffDay()
    thu: StaffDay = StaffDay()
    fri: StaffDay = StaffDay()
    sat: StaffDay = StaffDay()
    sun: StaffDay = StaffDay()
    no_online_booking: bool = False  # the bot never files a booking request to this person
    note: str = Field("", max_length=300)


class BotSettings(BaseModel):
    enabled: bool = True
    name: str = "Ассистент"
    # Business prompt: generated from a template (template_id + template_values) and then freely edited.
    # This is what the bot actually uses; while it is empty the engine does not reply (BotNotConfigured).
    template_id: str | None = None
    template_values: dict[str, str] = {}
    prompt: str = Field("", max_length=PROMPT_MAX)
    prompt_edited: bool = False  # edited manually since the last generation from the template
    hours: WeeklyHours = WeeklyHours()
    after_hours_mode: Literal["reply", "silent", "fixed_message"] = "reply"
    after_hours_message: str = ""
    greeting: str = ""
    buffer_delay_seconds: int = Field(8, ge=3, le=120)
    # how long the bot stays silent in a chat after a person (operator in the panel or on the phone) replied
    handover_pause_minutes: int = Field(720, ge=1, le=4320)
    # raw messages of the dialogue sent to the model (a client burst and the parts of a split reply count one
    # by one). 14, not 20: the client card carries the bookings, the name and lasting facts, so older
    # messages are rarely needed; a saved value is kept as is
    memory_messages: int = Field(14, ge=2, le=100)
    booking_confirm_template: str = "Ваша бронь подтверждена ✅ {details}"
    booking_reject_template: str = "К сожалению, не получилось забронировать: {reason}"
    booking_request_timeout_min: int = Field(15, ge=1, le=240)
    booking_timeout_message: str = "Администратор скоро свяжется с вами по заявке."
    # Sent once per conversation when the coins are used up (or model access is suspended) and the
    # conversation goes to the operator; empty = NO_COINS_MESSAGE_DEFAULT.
    no_coins_message: str = Field(NO_COINS_MESSAGE_DEFAULT, max_length=1000)
    # onboarding/knowledge
    services: list[ServiceItem] = Field(default_factory=list, max_length=SERVICES_MAX)
    staff: list[StaffMember] = Field(default_factory=list, max_length=STAFF_MAX)
    # voice message without text: ask to write in text, or hand the conversation to the operator
    voice_mode: Literal["ask_text", "handover"] = "ask_text"
    voice_message: str = Field(VOICE_MESSAGE_DEFAULT, max_length=500)
    # photo / video: thank and hand over to the operator (default), or only reply
    photo_mode: Literal["handover", "reply"] = "handover"
    photo_message: str = Field(PHOTO_MESSAGE_DEFAULT, max_length=500)

    @field_validator("services", mode="before")
    @classmethod
    def _legacy_services(cls, v: object) -> object:
        # onboarding/knowledge: very old settings had a free-text `services` field; turn it into rows
        if isinstance(v, str):
            from iler.knowledge.prices import parse_text

            return [r.as_dict() for r in parse_text(v).rows[:SERVICES_MAX]]
        return v

    # reply rules (iler.reply_rules): who gets a bot reply and when
    # Only contacts that did not write to the number before the bot was linked (see reply_rules.is_known).
    new_contacts_only: bool = False
    # Numbers the bot never answers (digits, KZ 8XXXXXXXXXX normalized to 7XXXXXXXXXX).
    exclude_phones: list[str] = []
    # Inside working hours (`hours`): "reply" as usual or "silent" (the staff answers during the day).
    # Outside working hours `after_hours_mode` applies.
    work_hours_mode: Literal["reply", "silent"] = "reply"
    # Time zone of `hours`; empty = the server default (ILER_TZ, Asia/Almaty).
    timezone: str = "Asia/Almaty"

    @field_validator("exclude_phones")
    @classmethod
    def _exclude_phones(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v:
            d = normalize_phone_digits(raw)
            if not d:
                continue
            if not 10 <= len(d) <= 15:
                raise ValueError(f"phone must have 10-15 digits: {raw[:32]}")
            if d not in out:
                out.append(d)
        if len(out) > EXCLUDE_PHONES_MAX:
            raise ValueError(f"at most {EXCLUDE_PHONES_MAX} excluded numbers")
        return out

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, v: str) -> str:
        v = (v or "").strip()
        if v:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"unknown time zone: {v[:64]}") from None
        return v

    @field_validator("template_values")
    @classmethod
    def _values(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > TEMPLATE_VALUES_MAX_KEYS:
            raise ValueError(f"at most {TEMPLATE_VALUES_MAX_KEYS} template values")
        for key, value in v.items():
            if len(key) > 64 or len(value) > TEMPLATE_VALUE_MAX:
                raise ValueError(f"template value too long: {key[:64]}")
        return v


# Claude models offered for the WhatsApp bot: Sonnet (smarter) or Haiku (faster, cheaper).
ANTHROPIC_MODELS = ("claude-sonnet-5-5", "claude-haiku-5-5")


class LlmSettings(BaseModel):
    # API keys are not settings: they are ILER's and come from the environment (config.Settings)
    provider: Literal["openai", "gemini", "anthropic"] = "openai"
    # defaults only; the admin UI offers the models listed by the provider for the owner's key
    openai_model: str = "gpt-5.4-mini"
    openai_fallback_model: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    anthropic_model: Literal["claude-sonnet-5-5", "claude-haiku-5-5"] = "claude-sonnet-5-5"
    # WhatsApp chat: low effort keeps replies fast; the owner can raise it
    anthropic_effort: Literal["low", "medium", "high"] = "low"
    emergency_provider: Literal["openai", "gemini", "anthropic"] | None = None

    @field_validator("anthropic_model", mode="before")
    @classmethod
    def _legacy_claude_model(cls, v: object) -> object:
        # settings saved before the Haiku/Sonnet restriction (e.g. claude-opus-5-5) move to Sonnet
        if isinstance(v, str) and v.startswith("claude-") and v not in ANTHROPIC_MODELS:
            return "claude-sonnet-5-5"
        return v


class SmtpSettings(BaseModel):
    host: str
    port: int = 587
    user: str = ""
    password: SecretStr | None = None
    sender: str = ""
    to: list[str] = []
    starttls: bool = True


class NotifySettings(BaseModel):
    admin_phones: list[str] = []
    admin_group_jid: str | None = None
    smtp: SmtpSettings | None = None

    # lead notifications (iler.notify): new requests and handovers also go to Telegram
    # The owner's own Telegram bot token: write-only in the API (GET says only whether it is set).
    telegram_bot_token: SecretStr | None = None
    telegram_chat_ids: list[str] = []
    # Address of this admin panel, for the "open the conversation" link in notifications.
    admin_url: str = Field("", max_length=300)

    @field_validator("telegram_chat_ids")
    @classmethod
    def _chat_ids(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v:
            value = str(raw).strip()
            if not value:
                continue
            if not re.fullmatch(r"-?\d{1,20}", value):
                raise ValueError(f"Telegram chat id must be a number: {value[:32]}")
            if value not in out:
                out.append(value)
        return out[:20]

    @field_validator("admin_url")
    @classmethod
    def _admin_url(cls, v: str) -> str:
        v = (v or "").strip().rstrip("/")
        if v and not re.match(r"^https?://[^\s/]+", v, re.IGNORECASE):
            raise ValueError("admin_url must start with http:// or https://")
        return v


class SenetSettings(BaseModel):
    enabled: bool = False
    orgname: str | None = None
    app_key: SecretStr | None = None


SETTINGS_KEYS: dict[str, type[BaseModel]] = {
    "bot": BotSettings,
    "llm": LlmSettings,
    "notify": NotifySettings,
    "senet": SenetSettings,
}


@dataclass
class AllSettings:
    bot: BotSettings
    llm: LlmSettings
    notify: NotifySettings
    senet: SenetSettings
