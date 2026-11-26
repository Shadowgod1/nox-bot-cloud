"""Session cookies, current client / staff, CSRF double-submit check, login lockout, audit log."""

import hmac
from dataclasses import dataclass

import asyncpg
from fastapi import Request, Response

from nox_portal import security
from nox_portal.config import cookies_secure
from nox_portal.db import get_pool
from nox_portal.errors import ApiError

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Lockout like iler-core: 10 consecutive failures lock the account for 15 minutes.
MAX_FAILURES = 10
LOCK_MINUTES = 15
_TABLES = {"client": "accounts", "staff": "staff"}


@dataclass
class Principal:
    kind: str  # client | staff
    id: int
    email: str


def set_session_cookies(response: Response, kind: str, subject_id: int, password_hash: str,
                        *, new_csrf: bool = True) -> None:
    secure = cookies_secure()
    response.set_cookie(
        security.SESSION_COOKIE, security.create_session(kind, subject_id, password_hash),
        max_age=security.SESSION_TTL, httponly=True, samesite="strict", path="/", secure=secure,
    )
    if new_csrf:
        # Readable by the SPA (not HttpOnly): it echoes the value in the X-Nox-CSRF header.
        response.set_cookie(
            security.CSRF_COOKIE, security.new_csrf_token(), max_age=security.SESSION_TTL,
            samesite="strict", path="/", secure=secure,
        )


def clear_session_cookies(response: Response) -> None:
    secure = cookies_secure()
    response.delete_cookie(security.SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=secure)
    response.delete_cookie(security.CSRF_COOKIE, path="/", samesite="strict", secure=secure)


async def _principal(request: Request, response: Response, kind: str) -> Principal:
    token = request.cookies.get(security.SESSION_COOKIE)
    if not token:
        raise ApiError(401, "unauthorized", "Требуется вход")
    try:
        claims = security.decode_session(token)
    except security.InvalidSession:
        raise ApiError(401, "unauthorized", "Сессия истекла") from None
    if claims["knd"] != kind:
        # a client session never opens staff endpoints (and vice versa)
        raise ApiError(403, "forbidden", "Недостаточно прав")
    table = _TABLES[kind]
    extra = ", disabled" if kind == "staff" else ""
    row = await get_pool().fetchrow(f"SELECT id, email, password_hash{extra} FROM {table} WHERE id = $1",
                                    claims["uid"])
    if row is None or not hmac.compare_digest(security.password_fingerprint(row["password_hash"]), claims["pwv"]):
        raise ApiError(401, "unauthorized", "Сессия истекла")
    if kind == "staff" and row["disabled"]:
        raise ApiError(401, "unauthorized", "Учётная запись отключена")
    if security.needs_refresh(claims):
        set_session_cookies(response, kind, row["id"], row["password_hash"], new_csrf=False)
        if request.cookies.get(security.CSRF_COOKIE):
            response.set_cookie(
                security.CSRF_COOKIE, request.cookies[security.CSRF_COOKIE], max_age=security.SESSION_TTL,
                samesite="strict", path="/", secure=cookies_secure(),
            )
    return Principal(kind=kind, id=row["id"], email=row["email"])


async def current_client(request: Request, response: Response) -> Principal:
    return await _principal(request, response, "client")


async def current_staff(request: Request, response: Response) -> Principal:
    return await _principal(request, response, "staff")


async def csrf_protect(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(security.CSRF_COOKIE, "")
    header = request.headers.get(security.CSRF_HEADER, "")
    if not cookie or not header or not hmac.compare_digest(cookie.encode(), header.encode()):
        raise ApiError(403, "csrf_failed", "CSRF-токен отсутствует или неверен")


# ---- lockout --------------------------------------------------------------------------------


async def is_locked(conn: asyncpg.Pool | asyncpg.Connection, kind: str, subject_id: int) -> bool:
    return bool(await conn.fetchval(
        f"SELECT locked_until IS NOT NULL AND locked_until > now() FROM {_TABLES[kind]} WHERE id = $1",
        subject_id,
    ))


async def register_failure(conn: asyncpg.Pool | asyncpg.Connection, kind: str, subject_id: int) -> bool:
    """Count a failed attempt atomically. Returns True when this failure locked the account."""
    return bool(await conn.fetchval(
        f"""
        UPDATE {_TABLES[kind]} SET
          locked_until = CASE WHEN failed_logins + 1 >= {MAX_FAILURES}
                              THEN now() + interval '{LOCK_MINUTES} minutes' ELSE locked_until END,
          failed_logins = CASE WHEN failed_logins + 1 >= {MAX_FAILURES} THEN 0 ELSE failed_logins + 1 END
        WHERE id = $1
        RETURNING locked_until IS NOT NULL AND locked_until > now()
        """,
        subject_id,
    ))


async def register_success(conn: asyncpg.Pool | asyncpg.Connection, kind: str, subject_id: int) -> None:
    await conn.execute(
        f"UPDATE {_TABLES[kind]} SET failed_logins = 0, locked_until = NULL, last_login_at = now() WHERE id = $1",
        subject_id,
    )


# ---- audit ----------------------------------------------------------------------------------


async def audit(conn: asyncpg.Pool | asyncpg.Connection, action: str, *, staff_id: int | None = None,
                account_id: int | None = None, details: dict | None = None, ip: str | None = None) -> None:
    await conn.execute(
        "INSERT INTO audit_log(staff_id, account_id, action, details, ip) VALUES ($1, $2, $3, $4, $5)",
        staff_id, account_id, action, details or {}, ip,
    )
