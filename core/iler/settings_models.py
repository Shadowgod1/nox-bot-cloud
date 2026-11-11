"""Typed settings stored in the `settings` table (one row per key)."""

import re
from dataclasses import dataclass
from typing import Literal

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
    handover_pause_minutes: int = Field(60, ge=1, le=1440)
    memory_messages: int = Field(20, ge=2, le=100)
    booking_confirm_template: str = "Ваша бронь подтверждена ✅ {details}"
    booking_reject_template: str = "К сожалению, не получилось забронировать: {reason}"
    booking_request_timeout_min: int = Field(15, ge=1, le=240)
    booking_timeout_message: str = "Администратор скоро свяжется с вами по заявке."

    @field_validator("template_values")
    @classmethod
    def _values(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > TEMPLATE_VALUES_MAX_KEYS:
            raise ValueError(f"at most {TEMPLATE_VALUES_MAX_KEYS} template values")
        for key, value in v.items():
            if len(key) > 64 or len(value) > TEMPLATE_VALUE_MAX:
                raise ValueError(f"template value too long: {key[:64]}")
        return v


class LlmSettings(BaseModel):
    provider: Literal["openai", "gemini", "anthropic"] = "openai"
    openai_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_fallback_model: str | None = None
    gemini_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    anthropic_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    # WhatsApp chat: low effort keeps replies fast; the owner can raise it
    anthropic_effort: Literal["low", "medium", "high"] = "low"
    emergency_provider: Literal["openai", "gemini", "anthropic"] | None = None


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
