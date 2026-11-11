"""User management (owner only)."""

import asyncpg
from fastapi import APIRouter, Depends

from iler.auth.deps import User, audit, csrf_protect, require_owner
from iler.auth.passwords import hash_password
from iler.auth.schemas import Ok, UserCreate, UserListItem
from iler.db.pool import get_pool
from iler.errors import ApiError

router = APIRouter(tags=["users"])


@router.get("/api/users", response_model=list[UserListItem])
async def get_users(_: User = Depends(require_owner)):
    rows = await get_pool().fetch(
        "SELECT id, email, name, role, created_at, last_login_at FROM users ORDER BY id"
    )
    return [UserListItem(**dict(r)) for r in rows]


@router.post("/api/users", response_model=User, dependencies=[Depends(csrf_protect)])
async def post_users(body: UserCreate, owner: User = Depends(require_owner)):
    password_hash = await hash_password(body.password)
    try:
        row = await get_pool().fetchrow(
            "INSERT INTO users(email, password_hash, name, role) VALUES ($1, $2, $3, $4)"
            " RETURNING id, email, name, role",
            body.email,
            password_hash,
            body.name,
            body.role,
        )
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "email_taken", "A user with this e-mail already exists") from None
    await audit(owner.id, "users.created", {"user_id": row["id"], "email": row["email"], "role": row["role"]})
    return User(**dict(row))


@router.delete("/api/users/{user_id}", response_model=Ok, dependencies=[Depends(csrf_protect)])
async def delete_users_user_id(user_id: int, owner: User = Depends(require_owner)):
    if user_id == owner.id:
        raise ApiError(409, "cannot_delete_self", "You cannot delete your own account")
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('iler_users'))")
        row = await conn.fetchrow("SELECT id, email, role FROM users WHERE id = $1", user_id)
        if row is None:
            raise ApiError(404, "not_found", "User not found")
        if row["role"] == "owner":
            owners = await conn.fetchval("SELECT count(*) FROM users WHERE role = 'owner'")
            if owners <= 1:
                raise ApiError(409, "last_owner", "Cannot delete the last owner")
        await conn.execute("DELETE FROM users WHERE id = $1", user_id)
    await audit(owner.id, "users.deleted", {"user_id": user_id, "email": row["email"]})
    return Ok()
