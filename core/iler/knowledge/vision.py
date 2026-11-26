"""Price list photo -> text lines, through a vision-capable model (Claude, or OpenAI as the second choice).

The request goes the same way as the bot's replies: through the LLM gateway (``/llm/anthropic/v1/messages``
or ``/llm/openai/v1/chat/completions`` pass image blocks through unchanged, body limit 10 MB) or directly
with ILER's key. It is logged to ``ai_requests`` like any LLM call, so its coins count towards the monthly
spend and the gateway balance; the coin limit / gateway block is checked first.
"""

import base64
import logging
import time

from iler import config, settings_store, usage
from iler.knowledge.prices import PriceImportError
from iler.llm import gateway, router
from iler.llm.base import LlmResponse
from iler.settings_models import LlmSettings

log = logging.getLogger("iler.knowledge.vision")

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Anthropic's per-image limit; keeps the gateway body under 10 MB
TIMEOUT_S = 90.0
NO_PRICES = "НЕТ ПРАЙСА"
PROMPT = (
    "На фото прайс-лист салона или клиники. Перепиши все услуги, по одной на строку, строго в формате:\n"
    "Название | Цена | Длительность\n"
    "Цену пиши как на фото, с «от», если оно есть, и с валютой. Длительность — только если указана, иначе "
    "оставь пусто. Заголовки разделов, телефоны, адреса и пояснения не пиши. Ничего, кроме этих строк, не "
    f"пиши. Если на фото нет прайса, ответь: {NO_PRICES}"
)
FAKE_TEXT = "Маникюр классический | от 5000 ₸ | 60 мин\nПокрытие гель-лак | 4000 ₸ | 45 мин\nПедикюр | от 8000 ₸ | 90 мин"
UNSUPPORTED = ("Распознавание фото прайса недоступно: для него нужна модель Claude или OpenAI. "
               "Загрузите CSV/XLSX или вставьте прайс текстом.")


def provider_for_photo() -> str | None:
    """Provider that will read the photo: anthropic first, then openai; None when neither has a key."""
    available = router.available_providers()
    for name in ("anthropic", "openai"):
        if name in available:
            return name
    return None


async def _call_anthropic(data: bytes, mime: str, settings: LlmSettings) -> LlmResponse:
    import anthropic

    key = router.vendor_key("anthropic")
    client = anthropic.AsyncAnthropic(
        api_key=key, timeout=TIMEOUT_S, max_retries=1,
        base_url=router.provider_base_url("anthropic") or "https://api.anthropic.com",
        default_headers={"x-api-key": key} if gateway.enabled() else None,
    )
    model = settings.anthropic_model
    resp = await client.messages.create(
        model=model, max_tokens=4096,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime,
                                         "data": base64.b64encode(data).decode()}},
            {"type": "text", "text": PROMPT},
        ]}],
    )
    text = "".join(b.text for b in resp.content or [] if getattr(b, "type", "") == "text")
    u = resp.usage
    tin = ((u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
           + (getattr(u, "cache_creation_input_tokens", 0) or 0)) if u else 0
    return LlmResponse(text=text, model=resp.model or model, provider="anthropic", input_tokens=tin,
                       output_tokens=(u.output_tokens or 0) if u else 0)


async def _call_openai(data: bytes, mime: str, settings: LlmSettings) -> LlmResponse:
    import openai

    client = openai.AsyncOpenAI(api_key=router.vendor_key("openai"), timeout=TIMEOUT_S, max_retries=1,
                                base_url=router.provider_base_url("openai"))
    model = settings.openai_model
    url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": url}},
        ]}],
    )
    text = (resp.choices[0].message.content or "") if resp.choices else ""
    u = resp.usage
    return LlmResponse(text=text, model=resp.model or model, provider="openai",
                       input_tokens=(u.prompt_tokens or 0) if u else 0,
                       output_tokens=(u.completion_tokens or 0) if u else 0)


async def read_price_photo(data: bytes, mime: str) -> tuple[str, float]:
    """Text lines "Название | Цена | Длительность" and the coins spent. Raises PriceImportError (Russian
    message) or usage.CoinsExhausted / usage.AccessSuspended."""
    if len(data) > MAX_IMAGE_BYTES:
        raise PriceImportError("Фото больше 5 МБ: уменьшите его или сделайте снимок экрана.")
    if config.get_settings().llm_fake:
        return FAKE_TEXT, 0.0
    name = provider_for_photo()
    if name is None:
        raise PriceImportError(UNSUPPORTED)
    if gateway.enabled():
        kind = await gateway.blocked()
        if kind:
            raise usage.AccessSuspended() if kind == gateway.KEY_REVOKED else usage.CoinsExhausted()
    elif await usage.limit_reached():
        raise usage.CoinsExhausted()
    settings = await settings_store.get("llm", LlmSettings)
    model = settings.anthropic_model if name == "anthropic" else settings.openai_model
    started = time.monotonic()
    try:
        resp = await (_call_anthropic if name == "anthropic" else _call_openai)(data, mime, settings)
    except Exception as e:  # noqa: BLE001 - SDK errors never leak (they may echo key fragments)
        status = getattr(e, "status_code", None)
        latency = int((time.monotonic() - started) * 1000)
        await router._log_request(None, name, model, None, latency, f"{name}: price photo: {type(e).__name__}")
        await usage.after_spend()
        log.warning("price photo recognition failed (%s): %s %s", name, type(e).__name__, status or "")
        if status == 402:
            if gateway.enabled():
                gateway.mark_blocked(gateway.INSUFFICIENT_COINS)
            raise usage.CoinsExhausted() from None
        if status == 400:
            raise PriceImportError("Модель не смогла прочитать это изображение. Попробуйте другое фото или CSV.") from None
        raise PriceImportError("Не удалось распознать фото: модель недоступна. Попробуйте позже или загрузите CSV/XLSX.") from None
    resp.latency_ms = int((time.monotonic() - started) * 1000)
    await router._log_request(None, name, model, resp, resp.latency_ms, None)
    await usage.after_spend()
    coins = round(usage.coins_for(resp.model or model, resp.input_tokens, resp.output_tokens), 3)
    return resp.text.strip(), coins
