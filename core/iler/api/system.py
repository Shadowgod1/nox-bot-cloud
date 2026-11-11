"""system API — stub from Task 1; implemented by Task 3."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/system/version")
async def get_system_version():
    not_implemented()


@router.post("/api/system/update")
async def post_system_update():
    not_implemented()


@router.get("/api/system/update/status")
async def get_system_update_status():
    not_implemented()
