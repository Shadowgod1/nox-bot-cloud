"""settings API — stub from Task 1; implemented by Task 2."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/settings/bot")
async def get_settings_bot():
    not_implemented()


@router.put("/api/settings/bot")
async def put_settings_bot():
    not_implemented()


@router.get("/api/settings/llm")
async def get_settings_llm():
    not_implemented()


@router.put("/api/settings/llm")
async def put_settings_llm():
    not_implemented()


@router.post("/api/settings/llm/test")
async def post_settings_llm_test():
    not_implemented()


@router.get("/api/settings/notify")
async def get_settings_notify():
    not_implemented()


@router.put("/api/settings/notify")
async def put_settings_notify():
    not_implemented()
