"""Evolution webhook — stub from Task 1; implemented by Task 4."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()


@router.post("/internal/evolution/webhook")
async def evolution_webhook():
    not_implemented()
