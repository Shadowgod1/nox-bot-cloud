"""users API — stub from Task 1; implemented by Task 2."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/users")
async def get_users():
    not_implemented()


@router.post("/api/users")
async def post_users():
    not_implemented()


@router.delete("/api/users/{user_id}")
async def delete_users_user_id(user_id: str):
    not_implemented()
