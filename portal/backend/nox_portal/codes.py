"""E-mail codes: 6 digits, valid 15 minutes, 5 attempts, a new one at most every 60 seconds.
Only an HMAC of the code is stored; issuing a new code invalidates the older ones of the same purpose."""

import hmac

import asyncpg

from nox_portal import security
from nox_portal.errors import ApiError

TTL_MINUTES = 15
MAX_ATTEMPTS = 5
COOLDOWN_SECONDS = 60


async def cooldown_left(conn: asyncpg.Pool | asyncpg.Connection, account_id: int, purpose: str) -> int:
    """Seconds until a new code may be sent (0 = now)."""
    left = await conn.fetchval(
        "SELECT ceil(extract(epoch FROM (max(created_at) + make_interval(secs => $3) - now())))::int"
        " FROM email_codes WHERE account_id = $1 AND purpose = $2",
        account_id, purpose, COOLDOWN_SECONDS,
    )
    return max(int(left or 0), 0)


async def issue(conn: asyncpg.Pool | asyncpg.Connection, account_id: int, purpose: str) -> str:
    code = security.new_code()
    await conn.execute(
        "UPDATE email_codes SET used_at = now() WHERE account_id = $1 AND purpose = $2 AND used_at IS NULL",
        account_id, purpose,
    )
    await conn.execute(
        "INSERT INTO email_codes(account_id, purpose, code_hash, expires_at)"
        " VALUES ($1, $2, $3, now() + make_interval(mins => $4))",
        account_id, purpose, security.hash_code(account_id, purpose, code), TTL_MINUTES,
    )
    return code


async def check(conn: asyncpg.Pool | asyncpg.Connection, account_id: int | None, purpose: str, code: str) -> None:
    """Consume the latest code or raise. Unknown accounts get the same answer as a wrong code."""
    row = None
    if account_id is not None:
        row = await conn.fetchrow(
            "SELECT id, code_hash, attempts, expires_at <= now() AS expired FROM email_codes"
            " WHERE account_id = $1 AND purpose = $2 AND used_at IS NULL ORDER BY created_at DESC LIMIT 1",
            account_id, purpose,
        )
    if row is None:
        raise ApiError(400, "invalid_code", "Неверный код. Запросите новый, если код устарел.")
    if row["expired"]:
        raise ApiError(400, "code_expired", "Срок действия кода истёк. Запросите новый код.")
    if row["attempts"] >= MAX_ATTEMPTS:
        raise ApiError(400, "code_exhausted", "Превышено число попыток. Запросите новый код.")
    expected = security.hash_code(account_id, purpose, code)  # type: ignore[arg-type]
    if not hmac.compare_digest(expected, row["code_hash"]):
        attempts = await conn.fetchval(
            "UPDATE email_codes SET attempts = attempts + 1 WHERE id = $1 RETURNING attempts", row["id"]
        )
        left = MAX_ATTEMPTS - attempts
        if left <= 0:
            raise ApiError(400, "code_exhausted", "Превышено число попыток. Запросите новый код.")
        raise ApiError(400, "invalid_code", f"Неверный код. Осталось попыток: {left}.")
    await conn.execute("UPDATE email_codes SET used_at = now() WHERE id = $1", row["id"])
