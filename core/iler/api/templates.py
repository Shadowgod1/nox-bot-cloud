"""templates API — stub from Task 1; implemented by Task 2."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/templates")
async def get_templates():
    not_implemented()


@router.post("/api/templates/{template_id}/apply")
async def post_templates_template_id_apply(template_id: str):
    not_implemented()
