"""First-run wizard: the owner account can be created exactly once, while no users exist.

Cloud instances get the owner from the Nox portal at start (iler.auth.preset_owner): then needs_setup is false,
the owner logs in with the portal password, and wizard_pending tells the admin UI to open the wizard after the
"owner" step until the owner finishes it (POST /api/setup/finish)."""

import hmac
import os

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel

from iler.auth import preset_owner
from iler.auth.deps import User, audit, csrf_protect, require_owner, set_session_cookies
from iler.auth.passwords import hash_password
from iler.auth.schemas import OwnerCreate
from iler.config import brand, get_settings
from iler.db.pool import get_pool
from iler.errors import ApiError

router = APIRouter(tags=["setup"])


class SetupState(BaseModel):
    needs_setup: bool
    # the owner came from the portal and has not finished the wizard yet: open it at the step after "owner"
    wizard_pending: bool = False


async def _state() -> SetupState:
    pool = get_pool()
    has_users = await pool.fetchval("SELECT EXISTS (SELECT 1 FROM users)")
    if not has_users:
        return SetupState(needs_setup=True)
    wizard = await preset_owner.wizard_state(pool)
    return SetupState(needs_setup=False, wizard_pending=bool(wizard.get("pending")))


@router.get("/api/setup/state", response_model=SetupState)
async def get_setup_state():
    return await _state()


@router.post("/api/setup/finish", response_model=SetupState, dependencies=[Depends(csrf_protect)])
async def post_setup_finish(owner: User = Depends(require_owner)):
    """The owner reached the end of the wizard (only matters for an owner created from the portal)."""
    if (await preset_owner.wizard_state(get_pool())).get("pending"):
        await preset_owner.finish_wizard(get_pool())
        await audit(owner.id, "setup.wizard_finished")
    return await _state()


class BrandInfo(BaseModel):
    brand: str


@router.get("/api/brand", response_model=BrandInfo)
async def get_brand():
    """Unauthenticated and DB-free: the admin SPA brands the login and setup screens with it."""
    return BrandInfo(brand=brand())


def check_setup_token(supplied: str | None) -> None:
    """The installer generates ILER_SETUP_TOKEN and prints it in the setup link, so only the person
    who ran the installer can create the owner. Without a configured token setup is refused (fail
    closed) unless ILER_ALLOW_INSECURE_SETUP=1 is set explicitly (development only)."""
    expected = get_settings().setup_token
    if not expected:
        if os.environ.get("ILER_ALLOW_INSECURE_SETUP") == "1":
            return
        raise ApiError(403, "setup_token_required", "Setup token is not configured on the server")
    if not supplied:
        raise ApiError(403, "setup_token_required", "Open the setup link printed by the installer")
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise ApiError(403, "setup_token_invalid", "Setup token is wrong")


@router.post("/api/setup/owner", response_model=User)
async def post_setup_owner(
    body: OwnerCreate,
    response: Response,
    x_iler_setup_token: str | None = Header(None, alias="X-ILER-Setup-Token"),
):
    """No CSRF check: there is no session yet, and the endpoint is dead once any user exists.
    Requires the one-time setup token from the installer (X-ILER-Setup-Token)."""
    if await get_pool().fetchval("SELECT EXISTS (SELECT 1 FROM users)"):
        raise ApiError(409, "already_set_up", "Setup is already complete")
    check_setup_token(x_iler_setup_token)
    password_hash = await hash_password(body.password)
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('iler_setup_owner'))")
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM users)"):
            raise ApiError(409, "already_set_up", "Setup is already complete")
        row = await conn.fetchrow(
            "INSERT INTO users(email, password_hash, name, role) VALUES ($1, $2, $3, 'owner')"
            " RETURNING id, email, name, role",
            body.email,
            password_hash,
            body.name,
        )
    set_session_cookies(response, row["id"], password_hash)
    await audit(row["id"], "setup.owner_created", {"email": row["email"]})
    return User(**dict(row))
