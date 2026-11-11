"""Read/write typed settings. SecretStr values are encrypted at rest as {"__enc__": token}."""

import logging
from typing import Any, TypeVar

from cryptography.exceptions import InvalidTag
from pydantic import BaseModel, SecretStr

from iler import crypto
from iler.db.pool import get_pool
from iler.settings_models import AllSettings, BotSettings, LlmSettings, NotifySettings, SenetSettings

T = TypeVar("T", bound=BaseModel)
ENC = "__enc__"
log = logging.getLogger("iler.settings")


def _encode(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return {ENC: crypto.encrypt(value.get_secret_value())}
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    return value


def _decrypt_or_none(token: Any) -> str | None:
    """A secret that cannot be decrypted (restored database, different ILER_SECRET_KEY) is treated
    as unset instead of breaking every settings read; the owner re-enters it in the admin UI."""
    try:
        return crypto.decrypt(token)
    except (InvalidTag, ValueError, TypeError):
        log.error("secret key mismatch: a stored secret cannot be decrypted with ILER_SECRET_KEY "
                  "(restore the original .env or re-enter the secret); treating it as unset")
        return None


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {ENC}:
            return _decrypt_or_none(value[ENC])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _jsonable(value: Any) -> Any:
    """model_dump(mode='python') but with datetimes/enums etc. converted; SecretStr kept as-is."""
    if isinstance(value, BaseModel):
        return {name: _jsonable(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, SecretStr):
        return value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    from pydantic_core import to_jsonable_python

    return to_jsonable_python(value)


async def get(key: str, model: type[T]) -> T:
    row = await get_pool().fetchrow("SELECT value FROM settings WHERE key = $1", key)
    if row is None:
        return model()
    return model.model_validate(_decode(row["value"]))


async def put(key: str, value: BaseModel) -> None:
    # Keep SecretStr objects (so _encode can encrypt them) but make everything else JSON-safe.
    payload = _encode(_jsonable(value))
    await get_pool().execute(
        "INSERT INTO settings(key, value, updated_at) VALUES ($1, $2, now())"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        key,
        payload,
    )


async def load_all() -> AllSettings:
    return AllSettings(
        bot=await get("bot", BotSettings),
        llm=await get("llm", LlmSettings),
        notify=await get("notify", NotifySettings),
        senet=await get("senet", SenetSettings),
    )
