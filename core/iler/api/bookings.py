"""Booking requests API (spec 7): list, confirm, reject."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from iler.auth.deps import User, audit, csrf_protect, current_user
from iler.bookings import service
from iler.bookings.service import BookingRequest
from iler.errors import ApiError

router = APIRouter(tags=["bookings"])


class DecisionBody(BaseModel):
    note: str | None = Field(None, max_length=1000)


@router.get("/api/bookings", response_model=list[BookingRequest])
async def get_bookings(
    status: Literal["pending", "confirmed", "rejected", "expired"] | None = Query(None),
    _: User = Depends(current_user),
):
    return await service.list_requests(status)


async def _decide(booking_id: int, status: Literal["confirmed", "rejected"], body: DecisionBody | None,
                  user: User) -> BookingRequest:
    note = body.note if body else None
    try:
        b = await service.decide(booking_id, status, note, user.name or user.email)
    except service.BookingNotFound:
        raise ApiError(404, "not_found", "Booking request not found") from None
    except service.BookingConflict as e:
        raise ApiError(409, "already_decided", f"Booking request is already {e.booking.status}") from None
    await audit(user.id, f"bookings.{status}", {"booking_id": booking_id, "note": note})
    return b


@router.post("/api/bookings/{booking_id}/confirm", response_model=BookingRequest,
             dependencies=[Depends(csrf_protect)])
async def post_bookings_booking_id_confirm(booking_id: int, body: DecisionBody | None = None,
                                           user: User = Depends(current_user)):
    return await _decide(booking_id, "confirmed", body, user)


@router.post("/api/bookings/{booking_id}/reject", response_model=BookingRequest,
             dependencies=[Depends(csrf_protect)])
async def post_bookings_booking_id_reject(booking_id: int, body: DecisionBody | None = None,
                                          user: User = Depends(current_user)):
    return await _decide(booking_id, "rejected", body, user)
