"""Runtime configuration. All variables use the NOX_ prefix (portal/.env.example lists them)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOX_", extra="ignore")

    database_url: str = "postgresql://portal:portal@db:5432/portal"
    # 32 random bytes, base64. HKDF-derived subkeys sign sessions, hash e-mail codes and encrypt setup links.
    secret_key: str = ""
    # Public address of the portal; an https:// address makes every cookie Secure.
    public_url: str = "https://bot.noxsw.kz"
    # Bots get https://<slug>.<instance_domain>
    instance_domain: str = "noxsw.kz"
    # Extra reserved slugs, comma separated (added to the built-in list).
    reserved_slugs: str = ""

    # E-mail: smtp (real), log (dev: print to the log, never in production), memory (tests)
    email_mode: str = "smtp"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # optional Reply-To of every letter (answers go to a human mailbox, e.g. info@noxsw.kz)
    smtp_reply_to: str = ""
    # starttls | ssl | none
    smtp_security: str = "starttls"
    # Staff addresses notified about new verified applications, comma separated.
    staff_notify: str = ""

    # Provisioner (host-side, root): sha256 hex of the shared token it sends in X-Nox-Provisioner-Token.
    # The portal never holds the token itself.
    provisioner_token_sha256: str = ""
    # RSA public key (PEM) of the provisioner: per-client LLM keys are sealed with it and only the
    # provisioner (root on the host) can open them.
    provisioner_pubkey_file: str = "/run/nox/provisioner-pub.pem"
    # A claimed job without a result after this many minutes is marked failed (staff may retry).
    job_timeout_minutes: int = 30

    # Seller requisites printed on invoices (placeholders in .env.example; real values only on the server)
    seller_name: str = "Nox service"
    seller_bin: str = ""
    seller_address: str = ""
    seller_bank: str = ""
    seller_iik: str = ""
    seller_bik: str = ""
    seller_kbe: str = ""
    seller_phone: str = ""
    # days the client has to pay an invoice (shown on it)
    invoice_due_days: int = 5

    # ---- Free test tariff (tariffs.FREE) ----
    # shown to clients in the tariff picker; false = only staff can assign it
    free_tariff_public: bool = True
    # at most this many Free accounts approved / provisioning / active at once
    free_max_active: int = 20
    # a Free account is suspended this many days after approval unless it moves to a paid tariff
    free_days: int = 7

    # ---- online payments (nox_portal/payments) ----
    # "" / none = online payment is off (only invoices via a manager); mock = fake checkout for tests
    payment_provider: str = ""
    # the mock provider works only with NOX_EMAIL_MODE=log or with this explicitly set to true
    payment_mock_allowed: bool = False
    # mock only: pretend the provider returns an opaque recurring token
    payment_mock_recurring: bool = False
    # kaspi ("Платежи на Kaspi.kz" service provider): QR / deeplink template with {account} and {amount}; the
    # exact URL comes from Kaspi with the contract. Empty = the kaspi provider is off.
    kaspi_pay_url_template: str = ""
    # Kaspi check/pay callbacks are accepted only from these IPs / CIDRs (comma separated; the visitor IP comes
    # from the portal nginx, i.e. CF-Connecting-IP behind the tunnel) ...
    kaspi_allowed_ips: str = ""
    # ... and/or with HTTP Basic auth "user:password" (if Kaspi supports it). At least one must be set.
    kaspi_basic_auth: str = ""

    # ---- LLM gateway (/llm/<provider>/...) ----
    # Nox company keys of the providers; a provider without a key answers 503. Only in this .env.
    upstream_anthropic_key: str = ""
    upstream_openai_key: str = ""
    upstream_gemini_key: str = ""
    # Upstream base URLs; overridden only by tests and the local smoke run (mock upstream)
    upstream_anthropic_base_url: str = "https://api.anthropic.com"
    upstream_openai_base_url: str = "https://api.openai.com"
    upstream_gemini_base_url: str = "https://generativelanguage.googleapis.com"
    # Price overrides, JSON {"model-prefix": [input_usd_per_mtok, output_usd_per_mtok]} (same as ILER_MODEL_PRICES)
    model_prices: str = ""
    # Requests per minute per virtual key, request body cap, upstream read timeout
    gateway_rate_per_minute: int = 60
    gateway_max_body_bytes: int = 10 * 1024 * 1024
    gateway_timeout_seconds: int = 600

    # ---- billing periods ----
    # days after period_end an unpaid renewal is tolerated before the account is suspended
    grace_days: int = 3
    # staff are reminded to issue the renewal invoice (and the client gets a reminder) this many days before the
    # period ends
    renewal_invoice_days: int = 5
    # background billing loop (renewals, rollover, suspension, daily summary); 0 = off (tests)
    billing_tick_seconds: int = 300
    # time zone of the daily summary and of usage-by-day in the cabinet
    tz: str = "Asia/Almaty"
    daily_summary_hour: int = 9

    # ---- provider failure alerts (staff e-mails from NOX_STAFF_NOTIFY + optional Telegram) ----
    alert_telegram_bot_token: str = ""
    alert_telegram_chat_id: str = ""
    alert_telegram_api: str = "https://api.telegram.org"
    # 5xx / timeouts from one provider within the window that trigger an alert
    alert_error_threshold: int = 5
    alert_window_seconds: int = 300
    # the same alert (provider + kind) is not repeated within this time
    alert_cooldown_seconds: int = 1800

    # ---- client-facing texts ----
    # coins one typical client dialog costs: tariff cards and the cabinet show "≈ N диалогов" (tariffs.dialogs)
    coins_per_dialog: int = 20
    # support contacts shown in the portal (header/footer, login panel, cabinet) and in the signature of letters;
    # empty = not shown. WhatsApp: the number in digits (wa.me link), e.g. 77001234567
    support_phone: str = ""
    support_whatsapp: str = ""
    # date of the published edition of /offer, /refund, /privacy (e.g. 01.12.2026); empty = the pages say "Черновик"
    legal_date: str = ""

    openapi: bool = False


def whatsapp_digits() -> str:
    """NOX_SUPPORT_WHATSAPP as digits only (+7 700 123-45-67 -> 77001234567); 8XXXXXXXXXX counts as 7XXXXXXXXXX."""
    digits = "".join(c for c in get_settings().support_whatsapp if c.isdigit())
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return digits


def whatsapp_url() -> str:
    digits = whatsapp_digits()
    return f"https://wa.me/{digits}" if digits else ""


def cookies_secure() -> bool:
    return get_settings().public_url.strip().lower().startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
