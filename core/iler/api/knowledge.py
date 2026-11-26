"""Price list import for "Услуги и мастера": parse a file / pasted text / photo into rows. Saves nothing:
the admin shows an editable preview and stores ``bot.services`` with PUT /api/settings/bot.

The file comes as base64 in JSON (no multipart dependency): CSV, XLSX, or a JPG / PNG / WEBP photo of the
price list (read by a vision model through the LLM gateway; costs coins, see iler.knowledge.vision).
"""

import base64
import binascii
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from iler.auth.deps import User, csrf_protect, current_user
from iler.config import brand_company, brand_name, gateway_mode
from iler.errors import ApiError
from iler.knowledge import prices

router = APIRouter(tags=["knowledge"])

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TEXT = 100_000


class PriceImportRequest(BaseModel):
    text: str | None = Field(None, max_length=MAX_TEXT)
    filename: str = Field("", max_length=255)
    content_base64: str | None = Field(None, max_length=MAX_FILE_BYTES * 4 // 3 + 16)


class PriceRowOut(BaseModel):
    name: str
    price: str
    duration_min: int | None


class PriceImportResponse(BaseModel):
    source: Literal["text", "csv", "xlsx", "image"]
    rows: list[PriceRowOut]
    warnings: list[str]
    coins: float | None = None  # photo: coins spent on recognition


class PhotoSupport(BaseModel):
    photo: bool  # a vision-capable provider is configured
    provider: str | None


@router.get("/api/knowledge/import-prices", response_model=PhotoSupport)
async def get_import_prices(_: User = Depends(current_user)):
    from iler.config import get_settings
    from iler.knowledge import vision

    name = "fake" if get_settings().llm_fake else vision.provider_for_photo()
    return {"photo": name is not None, "provider": name}


@router.post("/api/knowledge/import-prices", response_model=PriceImportResponse,
             dependencies=[Depends(csrf_protect)])
async def post_import_prices(body: PriceImportRequest, _: User = Depends(current_user)):
    coins = None
    try:
        if body.content_base64:
            try:
                data = base64.b64decode(body.content_base64, validate=True)
            except (binascii.Error, ValueError):
                raise ApiError(422, "validation_error", "Файл повреждён: загрузите его снова") from None
            if not data:
                raise ApiError(422, "validation_error", "Файл пустой")
            if len(data) > MAX_FILE_BYTES:
                raise ApiError(413, "file_too_large", "Файл больше 5 МБ")
            kind = prices.sniff_kind(body.filename, data)
            if kind == "image":
                from iler.knowledge import vision
                from iler.usage import AccessSuspended, CoinsExhausted

                try:
                    text, coins = await vision.read_price_photo(data, prices.image_mime(data) or "image/jpeg")
                except AccessSuspended:
                    raise ApiError(409, "llm_access_suspended",
                                   f"Доступ к моделям приостановлен — обратитесь в {brand_company()}") from None
                except CoinsExhausted:
                    raise ApiError(409, "coins_exhausted", f"Коины {brand_name()} закончились" if gateway_mode()
                                   else f"Лимит коинов {brand_name()} на этот месяц исчерпан") from None
                if not text or vision.NO_PRICES in text.upper():
                    raise prices.PriceImportError("На фото не нашли прайс. Сфотографируйте его ровнее и ближе.")
                result = prices.parse_text(text)
            elif kind == "xlsx":
                result = prices.parse_xlsx(data)
            else:
                result = prices.parse_csv(data)
            source = kind
        elif body.text is not None and body.text.strip():
            result = prices.parse_text(body.text)
            source = "text"
        else:
            raise ApiError(422, "validation_error", "Загрузите файл или вставьте текст прайса")
    except prices.PriceImportError as exc:
        raise ApiError(422, "price_import_failed", str(exc)) from None
    warnings = result.warnings()
    if not result.rows:
        warnings.insert(0, "Не нашли ни одной услуги с ценой. Проверьте, что в файле есть колонки «Услуга» и «Цена».")
    return {"source": source, "rows": [r.as_dict() for r in result.rows], "warnings": warnings, "coins": coins}
