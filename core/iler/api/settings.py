"""Bot / LLM / notification settings.

Secrets (SecretStr fields) never leave the server: GET returns them masked as "••••" + last 4 chars.
On PUT a secret field that is omitted or still masked keeps the stored value; null or "" clears it;
any other string replaces it.
"""

import inspect
import types
from typing import Any, Callable, Literal, Union, get_args, get_origin

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, SecretStr, ValidationError, create_model

from iler import settings_store
from iler.auth.deps import User, audit, csrf_protect, current_user, require_owner
from iler.errors import ApiError
from iler.settings_models import BotSettings, LlmSettings, NotifySettings, SmtpSettings

router = APIRouter(tags=["settings"])

MASK = "••••"


# --- secret masking -------------------------------------------------------------------------

def mask_secret(secret: SecretStr) -> str:
    value = secret.get_secret_value()
    return MASK + value[-4:] if len(value) >= 8 else MASK


def _mask(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return mask_secret(value)
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def masked(model: BaseModel) -> dict:
    return _mask(model.model_dump(mode="python"))


def _restore(incoming: dict, old: dict | None) -> dict:
    """Put stored secrets back where the client sent a masked value or omitted the field."""
    old = old or {}
    for key, old_value in old.items():
        if isinstance(old_value, SecretStr):
            new = incoming.get(key, ...)
            if new is ... or (isinstance(new, str) and new.startswith(MASK)):
                incoming[key] = old_value.get_secret_value()
        elif isinstance(old_value, dict) and isinstance(incoming.get(key), dict):
            incoming[key] = _restore(incoming[key], old_value)
    for key, new in list(incoming.items()):
        if isinstance(new, str) and new.startswith(MASK) and not isinstance(old.get(key), SecretStr):
            incoming[key] = None  # masked placeholder with nothing stored behind it
        elif isinstance(new, dict) and not isinstance(old.get(key), dict):
            incoming[key] = _restore(new, None)
    return incoming


def _drop_empty_secrets(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return value if value.get_secret_value() else None
    if isinstance(value, dict):
        return {k: _drop_empty_secrets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_drop_empty_secrets(v) for v in value]
    return value


def merge_secrets[T: BaseModel](cls: type[T], incoming: dict, stored: T) -> T:
    data = _restore(incoming, stored.model_dump(mode="python"))
    model = cls.model_validate(data)
    return cls.model_validate(_drop_empty_secrets(model.model_dump(mode="python")))


# --- public (masked) schemas for OpenAPI ------------------------------------------------------

def _public_type(tp: Any) -> Any:
    if tp is SecretStr:
        return str
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        return Union[tuple(_public_type(a) for a in get_args(tp))]  # noqa: UP007
    if origin is list:
        return list[_public_type(get_args(tp)[0])]
    if origin is Literal:
        return tp
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return public_model(tp)
    return tp


_PUBLIC: dict[type, type[BaseModel]] = {}


def public_model(cls: type[BaseModel]) -> type[BaseModel]:
    """Same fields as `cls`, but SecretStr becomes str (masked on output, plain or masked on input)."""
    if cls not in _PUBLIC:
        fields = {
            name: (_public_type(f.annotation), ... if f.is_required() else f.default)
            for name, f in cls.model_fields.items()
        }
        _PUBLIC[cls] = create_model(f"{cls.__name__}Public", **fields)
    return _PUBLIC[cls]


LlmPublic = public_model(LlmSettings)
NotifyPublic = public_model(NotifySettings)


def _validated[T: BaseModel](cls: type[T], incoming: dict, stored: T) -> T:
    try:
        return merge_secrets(cls, incoming, stored)
    except ValidationError as exc:
        from fastapi.exceptions import RequestValidationError

        raise RequestValidationError(exc.errors()) from None


# --- bot ----------------------------------------------------------------------------------------

@router.get("/api/settings/bot", response_model=BotSettings)
async def get_settings_bot(_: User = Depends(current_user)):
    return await settings_store.get("bot", BotSettings)


@router.put("/api/settings/bot", response_model=BotSettings, dependencies=[Depends(csrf_protect)])
async def put_settings_bot(body: BotSettings, user: User = Depends(current_user)):
    await settings_store.put("bot", body)
    await audit(user.id, "settings.bot_updated", {})
    return body


# --- llm (owner) --------------------------------------------------------------------------------

@router.get("/api/settings/llm", response_model=LlmPublic)
async def get_settings_llm(_: User = Depends(require_owner)):
    return masked(await settings_store.get("llm", LlmSettings))


@router.put("/api/settings/llm", response_model=LlmPublic, dependencies=[Depends(csrf_protect)])
async def put_settings_llm(body: LlmPublic, user: User = Depends(require_owner)):  # type: ignore[valid-type]
    stored = await settings_store.get("llm", LlmSettings)
    new = _validated(LlmSettings, body.model_dump(exclude_unset=True), stored)
    await settings_store.put("llm", new)
    await audit(user.id, "settings.llm_updated", {"provider": new.provider})
    return masked(new)


class LlmTestResult(BaseModel):
    ok: bool
    error: str | None = None


KeyChecker = Callable[[LlmSettings], Any]


def get_key_checker() -> KeyChecker:
    """Overridable dependency; the LLM router is imported lazily (it is optional at import time)."""
    from iler.llm.router import check_key

    return check_key


@router.post(
    "/api/settings/llm/test",
    response_model=LlmTestResult,
    response_model_exclude_none=True,
    dependencies=[Depends(csrf_protect)],
)
async def post_settings_llm_test(
    body: LlmPublic | None = Body(None),  # type: ignore[valid-type]
    _: User = Depends(require_owner),
    check_key: KeyChecker = Depends(get_key_checker),
):
    """Checks the stored settings, or unsaved form values if a body is sent (nothing is saved)."""
    from iler.llm.base import LlmError

    settings = await settings_store.get("llm", LlmSettings)
    if body is not None:
        settings = _validated(LlmSettings, body.model_dump(exclude_unset=True), settings)
    try:
        result = check_key(settings)
        if inspect.isawaitable(result):
            await result
    except LlmError as exc:
        return LlmTestResult(ok=False, error=str(exc) or "LLM check failed")
    return LlmTestResult(ok=True)


# --- notify -------------------------------------------------------------------------------------

_SMTP_TARGET = ("host", "port", "user")


def _require_smtp_password_on_target_change(incoming: dict, stored: NotifySettings) -> None:
    """A stored SMTP password is only reused for the same server and login. Otherwise anyone who can
    edit the form could point the host at their own server and receive the password in AUTH."""
    old = stored.smtp
    new = incoming.get("smtp")
    if old is None or old.password is None or not isinstance(new, dict):
        return
    password = new.get("password", ...)
    if not (password is ... or (isinstance(password, str) and password.startswith(MASK))):
        return  # a new value (or an explicit clear) was sent
    fields = SmtpSettings.model_fields
    for name in _SMTP_TARGET:
        value = new.get(name, fields[name].default)
        if value != getattr(old, name):
            raise ApiError(422, "smtp_password_required",
                           "Enter the SMTP password again after changing the server, port or login")


@router.get("/api/settings/notify", response_model=NotifyPublic)
async def get_settings_notify(_: User = Depends(require_owner)):
    return masked(await settings_store.get("notify", NotifySettings))


@router.put("/api/settings/notify", response_model=NotifyPublic, dependencies=[Depends(csrf_protect)])
async def put_settings_notify(body: NotifyPublic, user: User = Depends(require_owner)):  # type: ignore[valid-type]
    stored = await settings_store.get("notify", NotifySettings)
    incoming = body.model_dump(exclude_unset=True)
    _require_smtp_password_on_target_change(incoming, stored)
    new = _validated(NotifySettings, incoming, stored)
    await settings_store.put("notify", new)
    await audit(user.id, "settings.notify_updated", {})
    return masked(new)
