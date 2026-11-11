"""Runtime configuration. All variables use the ILER_ prefix."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ILER_", extra="ignore")

    database_url: str = "postgresql://iler:iler@postgres:5432/iler"
    # 32 random bytes, base64. Used for AES-GCM of stored secrets and to derive the session key.
    secret_key: str = ""
    evo_url: str = "http://evolution-api:8080"
    evo_api_key: str = ""
    evo_instance: str = "iler"
    # URL Evolution calls back on (inside the docker network).
    webhook_url: str = "http://iler-core:8000/internal/evolution/webhook"
    updater_url: str = "http://iler-updater:9000"
    updater_token: str = ""
    tz: str = "Asia/Almaty"
    data_dir: str = "/data"
    disable_workers: bool = False
    llm_fake: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
