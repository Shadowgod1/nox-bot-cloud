"""WhatsApp connection API (spec section 7): status, connect (QR), pairing code, logout."""

import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator

from iler.auth.deps import csrf_protect, current_user, require_owner
from iler.config import get_settings
from iler.errors import ApiError
from iler.whatsapp import get_gateway, monitor
from iler.whatsapp.base import WhatsAppGateway
from iler.whatsapp.evolution import EvolutionError, WrongState

router = APIRouter()


class WhatsAppStatus(BaseModel):
    status: str  # close | connecting | open | degraded
    phone: str | None = None
    last_error: str | None = None


class ConnectResult(BaseModel):
    qr_base64: str | None = None


class PairingRequest(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def _digits(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v or "")
        if not 10 <= len(digits) <= 15:
            raise ValueError("phone must have 10-15 digits")
        return digits


class PairingResult(BaseModel):
    code: str


class OkResult(BaseModel):
    ok: bool = True


def gateway_dep() -> WhatsAppGateway:
    return get_gateway()


def _unavailable(e: EvolutionError) -> ApiError:
    return ApiError(502, "evolution_unavailable", f"WhatsApp service error: {e}"[:300])


@router.get("/api/whatsapp/status", response_model=WhatsAppStatus)
async def get_whatsapp_status(_user=Depends(current_user)):
    return await monitor.get_status()


@router.post("/api/whatsapp/connect", response_model=ConnectResult, response_model_exclude_none=True)
async def post_whatsapp_connect(_user=Depends(require_owner), _csrf=Depends(csrf_protect),
                                gw: WhatsAppGateway = Depends(gateway_dep)):
    try:
        state = await gw.connection_state()
        if state == "open":
            raise ApiError(409, "already_connected", "WhatsApp is already connected")
        await gw.ensure_instance(get_settings().webhook_url)
        result = await gw.connect()
    except WrongState as e:
        raise ApiError(409, "already_connected", "WhatsApp is already connected") from e
    except EvolutionError as e:
        raise _unavailable(e) from e
    await monitor.set_status("connecting")
    return result


@router.post("/api/whatsapp/pairing", response_model=PairingResult)
async def post_whatsapp_pairing(body: PairingRequest, _user=Depends(require_owner), _csrf=Depends(csrf_protect),
                                gw: WhatsAppGateway = Depends(gateway_dep)):
    try:
        # Pairing only from `close` (spec 11.2); the instance is never deleted to get there.
        state = await gw.connection_state()
        if state != "close":
            raise ApiError(409, "wrong_state", f"Pairing is possible only when disconnected (now: {state})")
        await gw.ensure_instance(get_settings().webhook_url)
        code = await gw.pairing_code(body.phone)
    except WrongState as e:
        raise ApiError(409, "wrong_state", f"Pairing is possible only when disconnected (now: {e.state})") from e
    except EvolutionError as e:
        raise _unavailable(e) from e
    await monitor.set_status("connecting")
    return {"code": code}


@router.post("/api/whatsapp/logout", response_model=OkResult)
async def post_whatsapp_logout(_user=Depends(require_owner), _csrf=Depends(csrf_protect),
                               gw: WhatsAppGateway = Depends(gateway_dep)):
    try:
        await gw.logout()  # logout only: the instance itself is kept
    except EvolutionError as e:
        raise _unavailable(e) from e
    await monitor.set_status("close", phone=None, last_error=None, reset_failures=True)
    return {"ok": True}
