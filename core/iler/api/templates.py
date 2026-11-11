"""Bot templates: list and apply (apply overwrites the bot settings)."""

from fastapi import APIRouter, Depends

from iler import settings_store, templates_loader
from iler.auth.deps import User, audit, csrf_protect, current_user
from iler.errors import ApiError
from iler.settings_models import BotSettings

router = APIRouter(tags=["templates"])


@router.get("/api/templates", response_model=list[templates_loader.TemplateMeta])
async def get_templates(_: User = Depends(current_user)):
    return templates_loader.list_templates()


@router.post("/api/templates/{template_id}/apply", response_model=BotSettings, dependencies=[Depends(csrf_protect)])
async def post_templates_template_id_apply(template_id: str, user: User = Depends(current_user)):
    try:
        bot = templates_loader.load(template_id)
    except templates_loader.TemplateNotFound:
        raise ApiError(404, "not_found", "Template not found") from None
    except templates_loader.TemplateInvalid as exc:
        raise ApiError(500, "template_invalid", str(exc)) from None
    await settings_store.put("bot", bot)
    await audit(user.id, "templates.applied", {"template": template_id})
    return bot
