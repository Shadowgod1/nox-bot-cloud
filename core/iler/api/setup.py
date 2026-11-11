"""First-run wizard: the owner account can be created exactly once, while no users exist."""

from fastapi import APIRouter, Response
from pydantic import BaseModel

from iler.auth.deps import User, audit, set_session_cookies
from iler.auth.passwords import hash_password
from iler.auth.schemas import OwnerCreate
from iler.db.pool import get_pool
from iler.errors import ApiError

router = APIRouter(tags=["setup"])


class SetupState(BaseModel):
    needs_setup: bool


@router.get("/api/setup/state", response_model=SetupState)
async def get_setup_state():
    has_users = await get_pool().fetchval("SELECT EXISTS (SELECT 1 FROM users)")
    return SetupState(needs_setup=not has_users)


@router.post("/api/setup/owner", response_model=User)
async def post_setup_owner(body: OwnerCreate, response: Response):
    """No CSRF check: there is no session yet, and the endpoint is dead once any user exists."""
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
