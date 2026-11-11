"""Bot templates: list, and render a template's prompt from the client's field values.

Rendering saves nothing: the client reviews / edits the prompt and stores it via PUT /api/settings/bot.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from iler import templates_loader
from iler.auth.deps import User, csrf_protect, current_user
from iler.errors import ApiError

router = APIRouter(tags=["templates"])


class RenderRequest(BaseModel):
    values: dict = {}


class RenderResponse(BaseModel):
    prompt: str
    missing: list[str]


@router.get("/api/templates", response_model=list[templates_loader.TemplateMeta])
async def get_templates(_: User = Depends(current_user)):
    return templates_loader.list_templates()


@router.post("/api/templates/{template_id}/render", response_model=RenderResponse,
             dependencies=[Depends(csrf_protect)])
async def post_templates_template_id_render(template_id: str, body: RenderRequest, _: User = Depends(current_user)):
    try:
        template = templates_loader.load(template_id)
    except templates_loader.TemplateNotFound:
        raise ApiError(404, "not_found", "Template not found") from None
    except templates_loader.TemplateInvalid as exc:
        raise ApiError(500, "template_invalid", str(exc)) from None
    try:
        values = templates_loader.clean_values(template, body.values)
    except templates_loader.ValuesInvalid as exc:
        raise ApiError(422, "validation_error", str(exc)) from None
    return RenderResponse(
        prompt=templates_loader.render(template.prompt, values),
        missing=templates_loader.missing_required(template, values),
    )
