"""conversations API — stub from Task 1; implemented by Task 8."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/conversations")
async def get_conversations():
    not_implemented()


@router.get("/api/conversations/{conversation_id}/messages")
async def get_conversations_conversation_id_messages(conversation_id: str):
    not_implemented()


@router.post("/api/conversations/{conversation_id}/send")
async def post_conversations_conversation_id_send(conversation_id: str):
    not_implemented()


@router.post("/api/conversations/{conversation_id}/takeover")
async def post_conversations_conversation_id_takeover(conversation_id: str):
    not_implemented()


@router.post("/api/conversations/{conversation_id}/release")
async def post_conversations_conversation_id_release(conversation_id: str):
    not_implemented()
