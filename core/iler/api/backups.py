"""backups API — stub from Task 1; implemented by Task 3."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/backups")
async def get_backups():
    not_implemented()


@router.post("/api/backups")
async def post_backups():
    not_implemented()


@router.get("/api/backups/{name}")
async def get_backups_name(name: str):
    not_implemented()


@router.post("/api/backups/{name}/restore")
async def post_backups_name_restore(name: str):
    not_implemented()
