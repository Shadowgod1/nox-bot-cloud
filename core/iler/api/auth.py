"""auth API — stub from Task 1; implemented by Task 2."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.post("/api/auth/login")
async def post_auth_login():
    not_implemented()


@router.post("/api/auth/logout")
async def post_auth_logout():
    not_implemented()


@router.get("/api/auth/me")
async def get_auth_me():
    not_implemented()


@router.post("/api/auth/password")
async def post_auth_password():
    not_implemented()
