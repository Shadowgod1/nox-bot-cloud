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

class LlmProviders(BaseModel):
    providers: list[str]


@router.get("/api/settings/llm/providers", response_model=LlmProviders)
async def get_settings_llm_providers(_: User = Depends(require_owner)):
    """Providers that have an ILER key configured on this server (the only ones the owner can pick)."""
    from iler.llm import gateway
    from iler.llm.router import available_providers

    if gateway.enabled():
        await gateway.balance()  # cached; tells which providers the gateway has a company key for
    return {"providers": available_providers()}


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
    from iler.llm.base import LlmError, public_error

    settings = await settings_store.get("llm", LlmSettings)
    if body is not None:
        settings = _validated(LlmSettings, body.model_dump(exclude_unset=True), settings)
    try:
        result = check_key(settings)
        if inspect.isawaitable(result):
            await result
    except LlmError as exc:
        return LlmTestResult(ok=False, error=public_error(str(exc)) or "LLM check failed")
    return LlmTestResult(ok=True)


class LlmModelsResult(BaseModel):
    ok: bool
    models: list[str] = []
    error: str | None = None


ModelLister = Callable[[LlmSettings], Any]


def get_model_lister() -> ModelLister:
    from iler.llm.router import list_models

    return list_models


@router.post(
    "/api/settings/llm/models",
    response_model=LlmModelsResult,
    response_model_exclude_none=True,
    dependencies=[Depends(csrf_protect)],
)
async def post_settings_llm_models(
    body: LlmPublic | None = Body(None),  # type: ignore[valid-type]
    _: User = Depends(require_owner),
    list_models: ModelLister = Depends(get_model_lister),
):
    """Models of the primary provider for the stored settings or unsaved form values (nothing is saved).
    Listing models validates the key without spending tokens."""
    from iler.llm.base import LlmError, public_error

    settings = await settings_store.get("llm", LlmSettings)
    if body is not None:
        settings = _validated(LlmSettings, body.model_dump(exclude_unset=True), settings)
    try:
        models = await list_models(settings)
    except LlmError as exc:
        return LlmModelsResult(ok=False, error=public_error(str(exc)) or "LLM check failed")
    return LlmModelsResult(ok=True, models=models)


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


def _notify_out(n: NotifySettings) -> dict:
    """Masked settings; the Telegram bot token is write-only: only "set" (a bare mask) or null."""
    out = masked(n)
    if out.get("telegram_bot_token"):
        out["telegram_bot_token"] = MASK
    return out


@router.get("/api/settings/notify", response_model=NotifyPublic)
async def get_settings_notify(_: User = Depends(require_owner)):
    return _notify_out(await settings_store.get("notify", NotifySettings))


@router.put("/api/settings/notify", response_model=NotifyPublic, dependencies=[Depends(csrf_protect)])
async def put_settings_notify(body: NotifyPublic, user: User = Depends(require_owner)):  # type: ignore[valid-type]
    stored = await settings_store.get("notify", NotifySettings)
    incoming = body.model_dump(exclude_unset=True)
    _require_smtp_password_on_target_change(incoming, stored)
    new = _validated(NotifySettings, incoming, stored)
    await settings_store.put("notify", new)
    await audit(user.id, "settings.notify_updated", {})
    return _notify_out(new)


# --- reply rules ----------------------------------------------------------------------------------

class BotPauseRequest(BaseModel):
    paused: bool


class BotPauseState(BaseModel):
    enabled: bool


@router.get("/api/settings/bot/pause", response_model=BotPauseState)
async def get_bot_pause(_: User = Depends(current_user)):
    return {"enabled": (await settings_store.get("bot", BotSettings)).enabled}


@router.post("/api/settings/bot/pause", response_model=BotPauseState, dependencies=[Depends(csrf_protect)])
async def post_bot_pause(body: BotPauseRequest, user: User = Depends(current_user)):
    """The header switch "Бот на паузе": changes only `enabled`, so it cannot overwrite other settings."""
    bot = await settings_store.get("bot", BotSettings)
    bot.enabled = not body.paused
    await settings_store.put("bot", bot)
    await audit(user.id, "settings.bot_paused" if body.paused else "settings.bot_resumed", {})
    from iler import events

    events.publish("bot.status", {"enabled": bot.enabled})
    return {"enabled": bot.enabled}


class KnownContacts(BaseModel):
    count: int


@router.get("/api/settings/bot/known-contacts", response_model=KnownContacts)
async def get_known_contacts(_: User = Depends(current_user)):
    """How many chats the bot treats as "wrote before the bot was linked" (reply rule "only new contacts")."""
    from iler import reply_rules

    return {"count": await reply_rules.known_count()}


# --- lead notifications ---------------------------------------------------------------------------

class TelegramChatsRequest(BaseModel):
    # a newly typed token (not saved yet); empty or masked = the stored one
    token: str | None = None


class TelegramChat(BaseModel):
    id: str
    title: str


class TelegramChatsResult(BaseModel):
    ok: bool
    chats: list[TelegramChat] = []
    error: str | None = None


@router.post("/api/settings/notify/telegram/chats", response_model=TelegramChatsResult,
             response_model_exclude_none=True, dependencies=[Depends(csrf_protect)])
async def post_telegram_chats(body: TelegramChatsRequest, _: User = Depends(require_owner)):
    """Chats that wrote /start to the owner's Telegram bot, to pick where notifications go."""
    from iler import notify

    token = (body.token or "").strip()
    if not token or token.startswith(MASK):
        stored = (await settings_store.get("notify", NotifySettings)).telegram_bot_token
        token = stored.get_secret_value() if stored else ""
    if not token:
        return TelegramChatsResult(ok=False, error="укажите токен бота")
    try:
        chats = await notify.telegram_chats(token)
    except notify.TelegramError as e:
        return TelegramChatsResult(ok=False, error=str(e))
    return TelegramChatsResult(ok=True, chats=chats)


class ChannelResult(BaseModel):
    ok: bool
    sent: int = 0
    error: str | None = None


class NotifyTestResult(BaseModel):
    whatsapp: ChannelResult | None = None
    telegram: ChannelResult | None = None


TEST_TEXT = (
    "Проверка уведомлений {brand} Bot.\n"
    "Сюда будут приходить заявки клиентов, например:\n"
    "Новая заявка #15\n"
    "Имя: «Айгерим»\n"
    "Телефон: +7 701 123 45 67\n"
    "Услуга: «Маникюр с покрытием»\n"
    "Когда: завтра в 15:00\n"
    "{link}"
)


@router.post("/api/settings/notify/test", response_model=NotifyTestResult, dependencies=[Depends(csrf_protect)])
async def post_notify_test(user: User = Depends(require_owner)):
    """Send a sample notification to the saved admin numbers (from the connected WhatsApp number, to those
    numbers only) and Telegram chats. Channels that are not set up are left out of the answer."""
    import uuid

    from iler import notify
    from iler.config import brand_name
    from iler.engine import sender

    stored = await settings_store.get("notify", NotifySettings)
    base = stored.admin_url
    text = TEST_TEXT.format(brand=brand_name(), link=f"Диалог: {base}/conversations" if base else "").strip()
    result = NotifyTestResult()
    run = uuid.uuid4().hex[:12]  # every click is a new test message

    targets = sender.admin_targets(stored)
    if targets:
        statuses = [await sender.send_to_jid(f"admin:test:{run}:{jid}", jid, text) for jid in targets]
        sent = sum(s in ("sent", "uncertain") for s in statuses)
        result.whatsapp = ChannelResult(
            ok=sent == len(targets), sent=sent,
            error=None if sent == len(targets) else "WhatsApp не отправил сообщение — проверьте, что номер подключён",
        )
    if notify.telegram_configured(stored):
        token = stored.telegram_bot_token.get_secret_value()
        sent, error = 0, None
        for chat_id in stored.telegram_chat_ids:
            try:
                await notify.telegram_send(token, chat_id, text)
                sent += 1
            except notify.TelegramError as e:
                error = str(e)
        result.telegram = ChannelResult(ok=error is None, sent=sent, error=error)
    await audit(user.id, "settings.notify_tested", {})
    return result
