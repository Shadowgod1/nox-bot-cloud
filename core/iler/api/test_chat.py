"""test_chat API — stub from Task 1; implemented by Task 8."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.post("/api/test-chat")
async def post_test_chat():
    not_implemented()
