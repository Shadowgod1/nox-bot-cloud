"""whatsapp API — stub from Task 1; implemented by Task 4."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/whatsapp/status")
async def get_whatsapp_status():
    not_implemented()


@router.post("/api/whatsapp/connect")
async def post_whatsapp_connect():
    not_implemented()


@router.post("/api/whatsapp/pairing")
async def post_whatsapp_pairing():
    not_implemented()


@router.post("/api/whatsapp/logout")
async def post_whatsapp_logout():
    not_implemented()
