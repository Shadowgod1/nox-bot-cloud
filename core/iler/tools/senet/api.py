"""senet integration API — stub from Task 1; implemented by Task 11."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/integrations/senet")
async def get_integrations_senet():
    not_implemented()


@router.put("/api/integrations/senet")
async def put_integrations_senet():
    not_implemented()


@router.post("/api/integrations/senet/test")
async def post_integrations_senet_test():
    not_implemented()
