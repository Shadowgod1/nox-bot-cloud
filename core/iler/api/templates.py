"""Bot templates: list, and render a template's prompt from the client's field values.

Rendering saves nothing: the client reviews / edits the prompt and stores it via PUT /api/settings/bot.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from iler import templates_loader
from iler.auth.deps import User, csrf_protect, current_user
from iler.config import brand_company, brand_name, gateway_mode
from iler.errors import ApiError

router = APIRouter(tags=["templates"])


class RenderRequest(BaseModel):
    values: dict = {}


class RenderResponse(BaseModel):
    prompt: str
    missing: list[str]


class AiFillRequest(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=10, max_length=6000)


class AiFillResponse(BaseModel):
    values: dict[str, str]
    prompt: str
    missing: list[str]


def _load(template_id: str) -> templates_loader.Template:
    try:
        return templates_loader.load(template_id)
    except templates_loader.TemplateNotFound:
        raise ApiError(404, "not_found", "Template not found") from None
    except templates_loader.TemplateInvalid as exc:
        raise ApiError(500, "template_invalid", str(exc)) from None


@router.get("/api/templates", response_model=list[templates_loader.TemplateMeta])
async def get_templates(_: User = Depends(current_user)):
    return templates_loader.list_templates()


@router.post("/api/templates/{template_id}/render", response_model=RenderResponse,
             dependencies=[Depends(csrf_protect)])
async def post_templates_template_id_render(template_id: str, body: RenderRequest, _: User = Depends(current_user)):
    template = _load(template_id)
    try:
        values = templates_loader.clean_values(template, body.values)
    except templates_loader.ValuesInvalid as exc:
        raise ApiError(422, "validation_error", str(exc)) from None
    return RenderResponse(
        prompt=templates_loader.render(template.prompt, values),
        missing=templates_loader.missing_required(template, values),
    )


@router.post("/api/templates/{template_id}/ai-fill", response_model=AiFillResponse,
             dependencies=[Depends(csrf_protect)])
async def post_templates_template_id_ai_fill(template_id: str, body: AiFillRequest, _: User = Depends(current_user)):
    """The owner's own words about the business -> template field values (by the bot's model) and the prompt
    rendered from them. Saves nothing; spends coins like a test chat reply."""
    from iler.engine import ai_fill
    from iler.llm.base import LlmError, public_error, rejected_providers
    from iler.usage import AccessSuspended, CoinsExhausted

    template = _load(template_id)
    try:
        values = await ai_fill.fill(template, body.business_name.strip(), body.description.strip())
    except AccessSuspended as e:
        raise ApiError(409, "llm_access_suspended",
                       f"Доступ к моделям приостановлен — обратитесь в {brand_company()}") from e
    except CoinsExhausted as e:
        raise ApiError(409, "coins_exhausted", f"Коины {brand_name()} закончились" if gateway_mode()
                       else f"Лимит коинов {brand_name()} на этот месяц исчерпан") from e
    except LlmError as e:
        raise ApiError(424 if rejected_providers(str(e)) else 503, "llm_error", public_error(str(e))) from e
    # the template's typical texts (tone, FAQ...) stay where the owner said nothing about them
    filled = {f.key: (values.get(f.key) or f.default) for f in template.fields}
    filled = {k: v for k, v in filled.items() if v}
    return AiFillResponse(values=filled, prompt=templates_loader.render(template.prompt, filled),
                          missing=templates_loader.missing_required(template, filled))
