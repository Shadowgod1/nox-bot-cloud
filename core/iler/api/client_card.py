"""What the bot knows about the client of a conversation (engine.client_memory): view, delete one note,
forget everything. Deleting is what the privacy policy promises; booking requests are business records and
stay in "Заявки"."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from iler.auth.deps import User, audit, csrf_protect, current_user
from iler.db.pool import get_pool
from iler.errors import ApiError

router = APIRouter(tags=["conversations"])

PANEL_BOOKINGS = 5


class ClientBookingOut(BaseModel):
    id: int
    service: str
    when: str
    status: str
    created_at: datetime


class ClientCardOut(BaseModel):
    name: str = ""
    notes: list[str] = []
    bookings: list[ClientBookingOut] = []
    updated_at: datetime | None = None


class RemoveNote(BaseModel):
    fact: str = Field(min_length=1, max_length=500)


async def _conversation(conversation_id: int):
    row = await get_pool().fetchrow("SELECT id, phone, chat_jid FROM conversations WHERE id = $1", conversation_id)
    if row is None:
        raise ApiError(404, "not_found", "Conversation not found")
    return row


async def _card(conv) -> dict:
    from iler.engine import client_memory  # lazy: engine is optional at import time

    profile = await client_memory.get_profile(client_memory.contact_for(conv["phone"], conv["chat_jid"]))
    rows = await client_memory.recent_bookings(conv["id"], conv["phone"], PANEL_BOOKINGS)
    return {
        "name": profile["name"] if profile else "",
        "notes": client_memory.parse_notes(profile["notes"]) if profile else [],
        "bookings": [{"id": b.id, "service": b.service, "when": b.when, "status": b.status,
                      "created_at": b.created_at} for b in client_memory.booking_lines(rows, None)],
        "updated_at": profile["updated_at"] if profile else None,
    }


@router.get("/api/conversations/{conversation_id}/client", response_model=ClientCardOut)
async def get_client(conversation_id: int, _user: User = Depends(current_user)):
    return await _card(await _conversation(conversation_id))


@router.post("/api/conversations/{conversation_id}/client/notes/remove", response_model=ClientCardOut)
async def remove_note(conversation_id: int, body: RemoveNote, user: User = Depends(current_user),
                      _csrf=Depends(csrf_protect)):
    from iler.engine import client_memory

    conv = await _conversation(conversation_id)
    if not await client_memory.remove_fact(client_memory.contact_for(conv["phone"], conv["chat_jid"]), body.fact):
        raise ApiError(404, "not_found", "Note not found")
    await audit(user.id, "client.note_removed", {"conversation_id": conversation_id})  # no text: it is deleted
    return await _card(conv)


@router.delete("/api/conversations/{conversation_id}/client", response_model=ClientCardOut)
async def forget_client(conversation_id: int, user: User = Depends(current_user), _csrf=Depends(csrf_protect)):
    from iler.engine import client_memory

    conv = await _conversation(conversation_id)
    await client_memory.forget(client_memory.contact_for(conv["phone"], conv["chat_jid"]))
    await audit(user.id, "client.forgotten", {"conversation_id": conversation_id})
    return await _card(conv)
