"""Login / logout / current user / password change."""

from fastapi import APIRouter, Depends, Response

from iler.auth import lockout
from iler.auth.deps import User, audit, clear_session_cookies, csrf_protect, current_user, set_session_cookies
from iler.auth.passwords import hash_password, verify_password
from iler.auth.schemas import Credentials, Ok, PasswordChange
from iler.db.pool import get_pool
from iler.errors import ApiError

router = APIRouter(tags=["auth"])


@router.post("/api/auth/login", response_model=User)
async def post_auth_login(body: Credentials, response: Response):
    """No CSRF check (no session yet); the session cookie is SameSite=Strict."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, email, name, role, password_hash FROM users WHERE email = $1", body.email
    )
    if row is None:
        await verify_password(None, body.password)  # equalize timing
        raise ApiError(401, "invalid_credentials", "Wrong e-mail or password")
    if await lockout.check_locked(pool, row["id"]):
        raise ApiError(429, "locked", "Too many failed attempts, try again later")
    if not await verify_password(row["password_hash"], body.password):
        if await lockout.register_failure(pool, row["id"]):
            await audit(row["id"], "auth.locked", {})
        raise ApiError(401, "invalid_credentials", "Wrong e-mail or password")
    await lockout.register_success(pool, row["id"])
    set_session_cookies(response, row["id"], row["password_hash"])
    return User(id=row["id"], email=row["email"], name=row["name"], role=row["role"])


@router.post("/api/auth/logout", response_model=Ok)
async def post_auth_logout(response: Response):
    clear_session_cookies(response)
    return Ok()


@router.get("/api/auth/me", response_model=User)
async def get_auth_me(user: User = Depends(current_user)):
    return user


@router.post("/api/auth/password", response_model=Ok, dependencies=[Depends(csrf_protect)])
async def post_auth_password(body: PasswordChange, response: Response, user: User = Depends(current_user)):
    pool = get_pool()
    old_hash = await pool.fetchval("SELECT password_hash FROM users WHERE id = $1", user.id)
    if not await verify_password(old_hash, body.old):
        raise ApiError(401, "invalid_credentials", "Current password is wrong")
    new_hash = await hash_password(body.new)
    await pool.execute("UPDATE users SET password_hash = $1 WHERE id = $2", new_hash, user.id)
    set_session_cookies(response, user.id, new_hash, new_csrf=False)  # other sessions are now invalid
    await audit(user.id, "auth.password_changed", {})
    return Ok()
