"""Request / response models. Responses never carry password hashes, codes, LLM keys or sealed blobs."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from nox_portal import slugs
from nox_portal.security import MAX_PASSWORD, validate_password

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9 ()-]{7,24}$")
_LLM_KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9._-]{16,240}$")


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL_RE.match(value):
        raise ValueError("неверный e-mail")
    return value


def _phone(value: str) -> str:
    value = value.strip()
    if not _PHONE_RE.match(value) or sum(c.isdigit() for c in value) < 7:
        raise ValueError("неверный номер телефона")
    return value


def _slug(value: str) -> str:
    value = slugs.normalize(value)
    err = slugs.check(value)
    if err:
        raise ValueError(err)
    return value


def _text(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("поле не может быть пустым")
    return value


def _consent(value: bool) -> bool:
    if value is not True:
        raise ValueError("нужно согласие на обработку персональных данных")
    return value


def _code(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[0-9]{6}", value):
        raise ValueError("код — 6 цифр")
    return value


def _llm_key(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not _LLM_KEY_RE.match(value):
        raise ValueError("ключ Anthropic должен начинаться с sk-ant- и содержать только латиницу, цифры, . _ -")
    return value


Email = Annotated[str, AfterValidator(_email)]
LoginEmail = Annotated[str, Field(max_length=320), AfterValidator(lambda v: v.strip().lower())]
NewPassword = Annotated[str, AfterValidator(validate_password)]
Slug = Annotated[str, Field(max_length=64), AfterValidator(_slug)]
Code = Annotated[str, Field(max_length=12), AfterValidator(_code)]
Text200 = Annotated[str, Field(max_length=200), AfterValidator(_text)]


class Register(BaseModel):
    company: Text200
    contact_name: Text200
    email: Email
    phone: Annotated[str, Field(max_length=32), AfterValidator(_phone)]
    slug: Slug
    password: NewPassword
    consent: Annotated[bool, AfterValidator(_consent)]


class EmailOnly(BaseModel):
    email: LoginEmail


class Verify(BaseModel):
    email: LoginEmail
    code: Code


class Credentials(BaseModel):
    email: LoginEmail
    password: str = Field(max_length=MAX_PASSWORD)


class ResetConfirm(BaseModel):
    email: LoginEmail
    code: Code
    password: NewPassword


class Ok(BaseModel):
    ok: bool = True


class SlugCheck(BaseModel):
    slug: str
    available: bool
    message: str | None = None


Status = Literal["new", "verified", "approved", "provisioning", "active", "failed", "rejected",
                 "suspended", "removed"]


class ClientMe(BaseModel):
    kind: Literal["client"] = "client"
    email: str
    company: str
    contact_name: str
    phone: str
    slug: str
    bot_host: str
    status: Status
    admin_url: str | None
    reject_reason: str | None
    setup_link_available: bool
    created_at: datetime


class SetupLink(BaseModel):
    setup_url: str


class StaffMe(BaseModel):
    kind: Literal["staff"] = "staff"
    id: int
    email: str
    name: str


class Approve(BaseModel):
    coins: int = Field(ge=0, le=999_999_999)
    slug: Slug | None = None
    # write-only: sealed for the provisioner immediately, never stored in clear or returned
    anthropic_key: Annotated[str | None, Field(max_length=256), AfterValidator(_llm_key)] = None


class Reject(BaseModel):
    reason: Annotated[str, Field(min_length=3, max_length=1000)]


class RemoveConfirm(BaseModel):
    confirm_slug: str = Field(max_length=64)


class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    slug: str
    coins: int | None
    has_custom_key: bool
    error: str | None
    created_at: datetime
    claimed_at: datetime | None
    finished_at: datetime | None


class AuditOut(BaseModel):
    id: int
    action: str
    staff_email: str | None
    account_id: int | None
    company: str | None
    details: dict
    ip: str | None
    created_at: datetime


class ApplicationListItem(BaseModel):
    id: int
    company: str
    contact_name: str
    email: str
    phone: str
    slug: str
    status: Status
    coins_limit: int | None
    created_at: datetime
    updated_at: datetime


class ApplicationDetail(ApplicationListItem):
    bot_host: str
    admin_url: str | None
    port: int | None
    reject_reason: str | None
    last_error: str | None
    email_verified_at: datetime | None
    register_ip: str | None
    jobs: list[JobOut]
    audit: list[AuditOut]


class StaffConfig(BaseModel):
    coin_presets: list[int]
    instance_domain: str
    custom_key_supported: bool


# ---- internal (provisioner) -----------------------------------------------------------------


class ClaimedJob(BaseModel):
    id: int
    kind: str
    slug: str
    coins: int | None
    public_url: str
    # base64 RSA-OAEP ciphertext for the provisioner's private key, or None (use the default key)
    llm_key_sealed: str | None


class JobResult(BaseModel):
    ok: bool
    port: int | None = Field(None, ge=1, le=65535)
    admin_url: Annotated[str | None, Field(max_length=300)] = None
    setup_url: Annotated[str | None, Field(max_length=500)] = None
    error: Annotated[str | None, Field(max_length=2000)] = None
