"""Login lockout: 10 consecutive failures lock the account for 15 minutes (state in `users`)."""

import asyncpg

MAX_FAILURES = 10
LOCK_MINUTES = 15


async def check_locked(conn: asyncpg.Pool | asyncpg.Connection, user_id: int) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT locked_until IS NOT NULL AND locked_until > now() FROM users WHERE id = $1", user_id
        )
    )


async def register_failure(conn: asyncpg.Pool | asyncpg.Connection, user_id: int) -> bool:
    """Count a failed attempt atomically. Returns True when this failure locked the account."""
    locked = await conn.fetchval(
        f"""
        UPDATE users SET
          locked_until = CASE WHEN failed_logins + 1 >= {MAX_FAILURES}
                              THEN now() + interval '{LOCK_MINUTES} minutes' ELSE locked_until END,
          failed_logins = CASE WHEN failed_logins + 1 >= {MAX_FAILURES} THEN 0 ELSE failed_logins + 1 END
        WHERE id = $1
        RETURNING locked_until IS NOT NULL AND locked_until > now()
        """,
        user_id,
    )
    return bool(locked)


async def register_success(conn: asyncpg.Pool | asyncpg.Connection, user_id: int) -> None:
    await conn.execute(
        "UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = now() WHERE id = $1", user_id
    )
