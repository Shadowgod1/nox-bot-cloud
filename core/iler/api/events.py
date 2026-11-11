"""events API — stub from Task 1; implemented by Task 8."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/events")
async def get_events():
    not_implemented()
