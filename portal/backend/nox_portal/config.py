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

    openapi: bool = False


def cookies_secure() -> bool:
    return get_settings().public_url.strip().lower().startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
