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


Status = Literal["new", "verified", "awaiting_payment", "approved", "provisioning", "active", "failed",
                 "rejected", "suspended", "removed"]


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
    tariff: str | None
    invoice_no: str | None
    invoice_amount: int | None
    created_at: datetime
    # the bot uses the Nox LLM gateway: balance, usage and top-ups are shown in the cabinet
    llm_enabled: bool = False
    local_install: bool = False


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


class MarkPaid(Approve):
    """Record a payment (ledger row) and approve in the same transaction; coins default to the tariff's."""
    coins: int | None = Field(None, ge=0, le=999_999_999)  # type: ignore[assignment]
    amount_kzt: int = Field(gt=0, le=1_000_000_000)
    reference: str = Field("", max_length=200)
    note: str = Field("", max_length=1000)


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
    has_gateway_key: bool = False
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
    tariff: str | None
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
    invoice_no: str | None
    invoice_amount: int | None
    invoice_issued_at: datetime | None
    local_install: bool = False
    llm: "LlmBalance | None" = None
    keys: list["KeyOut"] = []
    invoices: list["InvoiceListItem"] = []
    payments: list["PaymentOut"]
    jobs: list[JobOut]
    audit: list[AuditOut]


class TariffOut(BaseModel):
    code: str
    name: str
    price_kzt: int
    coins: int
    description: str


class TariffChoice(BaseModel):
    tariff: str = Field(max_length=32)


class Seller(BaseModel):
    name: str
    bin: str
    address: str
    bank: str
    iik: str
    bik: str
    kbe: str
    phone: str


class Invoice(BaseModel):
    number: str
    issued_at: datetime
    due_days: int
    amount_kzt: int
    tariff: TariffOut
    buyer_company: str
    buyer_contact: str
    buyer_email: str
    buyer_phone: str
    bot_host: str
    seller: Seller
    paid: bool
    # subscription: the first invoice; renewal: next month; topup: coin pack
    kind: Literal["subscription", "renewal", "topup"] = "subscription"
    period_start: datetime | None = None
    period_end: datetime | None = None


class PaymentOut(BaseModel):
    id: int
    invoice_no: str | None
    tariff: str | None
    amount_kzt: int
    method: str
    reference: str
    note: str
    staff_email: str | None
    created_at: datetime


class StaffConfig(BaseModel):
    tariffs: list[TariffOut]
    coin_presets: list[int]
    instance_domain: str
    custom_key_supported: bool
    topup: TariffOut | None = None
    gateway_url: str = ""


# ---- LLM gateway: balance, usage, keys, invoices ----------------------------------------------


class LlmBalance(BaseModel):
    coins_left: int
    monthly_limit: int
    monthly_coins: float
    topup_coins: float
    spent: float
    period_start: datetime | None
    period_end: datetime | None
    status: Literal["active", "suspended", "revoked"]


class UsageDay(BaseModel):
    day: str
    coins: float


class UsageModel(BaseModel):
    provider: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    coins: float


class UsageOut(BaseModel):
    balance: LlmBalance
    by_day: list[UsageDay]
    by_model: list[UsageModel]


class KeyOut(BaseModel):
    id: int
    prefix: str
    status: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class InvoiceListItem(BaseModel):
    id: int | None
    number: str
    kind: Literal["subscription", "renewal", "topup"]
    tariff: str | None
    amount_kzt: int
    coins: int | None
    issued_at: datetime | None
    period_start: datetime | None
    paid: bool


class MarkInvoicePaid(BaseModel):
    amount_kzt: int = Field(gt=0, le=1_000_000_000)
    reference: str = Field("", max_length=200)
    note: str = Field("", max_length=1000)


class KeyIssued(BaseModel):
    ok: bool = True
    prefix: str
    # the full key, only for local installs (shown once); cloud keys travel sealed to the provisioner
    key: str | None = None
    job_id: int | None = None


class LocalAccount(BaseModel):
    company: Text200
    contact_name: Text200
    email: Email
    phone: Annotated[str, Field(max_length=32), AfterValidator(_phone)]
    slug: Slug
    tariff: str = Field(max_length=32)
    coins: int | None = Field(None, ge=0, le=999_999_999)


class LocalAccountCreated(BaseModel):
    account_id: int
    prefix: str
    key: str


# ---- internal (provisioner) -----------------------------------------------------------------


class ClaimedJob(BaseModel):
    id: int
    kind: str
    slug: str
    coins: int | None
    public_url: str
    # base64 RSA-OAEP ciphertext for the provisioner's private key, or None (use the default key)
    llm_key_sealed: str | None
    # the account's LLM gateway virtual key (nox_vk_...), sealed the same way; create / rekey jobs.
    # A per-client llm_key_sealed takes precedence (the instance then talks to Anthropic directly).
    gateway_key_sealed: str | None = None


class JobResult(BaseModel):
    ok: bool
    port: int | None = Field(None, ge=1, le=65535)
    admin_url: Annotated[str | None, Field(max_length=300)] = None
    setup_url: Annotated[str | None, Field(max_length=500)] = None
    error: Annotated[str | None, Field(max_length=2000)] = None


ApplicationDetail.model_rebuild()
