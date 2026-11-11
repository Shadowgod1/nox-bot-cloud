"""Typed settings stored in the `settings` table (one row per key)."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class ServiceItem(BaseModel):
    name: str
    price: str = ""
    note: str = ""


class FaqItem(BaseModel):
    q: str
    a: str


class DayHours(BaseModel):
    open: str = "00:00"   # HH:MM
    close: str = "24:00"  # HH:MM, "24:00" = end of day
    closed: bool = False


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
    persona: str = ""
    instructions: str = ""
    services: list[ServiceItem] = []
    faq: list[FaqItem] = []
    address: str = ""
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


class LlmSettings(BaseModel):
    provider: Literal["openai", "gemini"] = "openai"
    openai_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_fallback_model: str | None = None
    gemini_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    emergency_provider: Literal["openai", "gemini"] | None = None


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
