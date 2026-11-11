"""bookings API — stub from Task 1; implemented by Task 7."""

from fastapi import APIRouter

from iler.errors import not_implemented

router = APIRouter()

@router.get("/api/bookings")
async def get_bookings():
    not_implemented()


@router.post("/api/bookings/{booking_id}/confirm")
async def post_bookings_booking_id_confirm(booking_id: str):
    not_implemented()


@router.post("/api/bookings/{booking_id}/reject")
async def post_bookings_booking_id_reject(booking_id: str):
    not_implemented()
