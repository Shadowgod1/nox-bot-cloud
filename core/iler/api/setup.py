"""setup API — stub from Task 1; implemented by Task 2."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/setup/state")
async def get_setup_state():
    not_implemented()


@router.post("/api/setup/owner")
async def post_setup_owner():
    not_implemented()
