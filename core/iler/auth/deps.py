"""FastAPI dependencies: current user (sliding session), owner role, CSRF double-submit check."""

import hmac
from typing import Literal

from fastapi import Depends, Request, Response
from pydantic import BaseModel

from iler.auth import tokens
from iler.config import cookies_secure
from iler.db.pool import get_pool
from iler.errors import ApiError

Role = Literal["owner", "operator"]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class User(BaseModel):
    id: int
    email: str
    name: str
    role: Role


def set_session_cookies(response: Response, user_id: int, password_hash: str, *, new_csrf: bool = True) -> None:
    response.set_cookie(
        tokens.SESSION_COOKIE,
        tokens.create_session(user_id, password_hash),
        max_age=tokens.SESSION_TTL,
        httponly=True,
        samesite="strict",
        path="/",
        secure=cookies_secure(),
    )
    if new_csrf:
        # Readable by the SPA (not HttpOnly): it echoes the value in the X-ILER-CSRF header.
        response.set_cookie(
            tokens.CSRF_COOKIE, tokens.new_csrf_token(), max_age=tokens.SESSION_TTL, samesite="strict", path="/",
            secure=cookies_secure(),
        )


def clear_session_cookies(response: Response) -> None:
    secure = cookies_secure()
    response.delete_cookie(tokens.SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=secure)
    response.delete_cookie(tokens.CSRF_COOKIE, path="/", samesite="strict", secure=secure)


async def current_user(request: Request, response: Response) -> User:
    token = request.cookies.get(tokens.SESSION_COOKIE)
    if not token:
        raise ApiError(401, "unauthorized", "Войдите в панель, чтобы продолжить")
    try:
        claims = tokens.decode_session(token)
    except tokens.InvalidSession:
        raise ApiError(401, "unauthorized", "Сессия истекла, войдите снова") from None
    row = await get_pool().fetchrow(
        "SELECT id, email, name, role, password_hash FROM users WHERE id = $1", claims["uid"]
    )
    if row is None or not hmac.compare_digest(tokens.password_fingerprint(row["password_hash"]), claims["pwv"]):
        raise ApiError(401, "unauthorized", "Сессия истекла, войдите снова")
    if tokens.needs_refresh(claims):
        set_session_cookies(response, row["id"], row["password_hash"], new_csrf=False)
        if request.cookies.get(tokens.CSRF_COOKIE):  # extend the CSRF cookie alongside the session
            response.set_cookie(
                tokens.CSRF_COOKIE, request.cookies[tokens.CSRF_COOKIE], max_age=tokens.SESSION_TTL,
                samesite="strict", path="/", secure=cookies_secure(),
            )
    return User(id=row["id"], email=row["email"], name=row["name"], role=row["role"])


async def require_owner(user: User = Depends(current_user)) -> User:
    if user.role != "owner":
        raise ApiError(403, "forbidden", "Это может сделать только владелец")
    return user


async def csrf_protect(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(tokens.CSRF_COOKIE, "")
    header = request.headers.get(tokens.CSRF_HEADER, "")
    if not cookie or not header or not hmac.compare_digest(cookie.encode(), header.encode()):
        raise ApiError(403, "csrf_failed", "Сессия устарела, обновите страницу")


async def audit(user_id: int | None, action: str, details: dict | None = None) -> None:
    await get_pool().execute(
        "INSERT INTO audit_log(user_id, action, details) VALUES ($1, $2, $3)",
        user_id,
        action,
        details or {},
    )

