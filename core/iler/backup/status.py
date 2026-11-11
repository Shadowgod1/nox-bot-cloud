"""Last backup outcome, kept in the settings table under key ``backup_status``.

Shape: ``{"status": "ok" | "error", "at": ISO-8601, "message": str | None}``.
"""

import logging
from datetime import UTC, datetime

from iler.db.pool import get_pool

log = logging.getLogger("iler.backup")

KEY = "backup_status"
EMPTY = {"status": None, "at": None, "message": None}


async def record(ok: bool, message: str | None = None) -> dict:
    value = {
        "status": "ok" if ok else "error",
        "at": datetime.now(UTC).isoformat(),
        "message": (message or None) and message[:500],
    }
    await get_pool().execute(
        "INSERT INTO settings(key, value, updated_at) VALUES ($1, $2, now())"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        KEY,
        value,
    )
    return value


async def safe_record(ok: bool, message: str | None = None) -> None:
    try:
        await record(ok, message)
    except Exception:  # noqa: BLE001 - bookkeeping must never break a backup
        log.exception("could not store backup status")


async def get() -> dict:
    value = await get_pool().fetchval("SELECT value FROM settings WHERE key = $1", KEY)
    if not isinstance(value, dict):
        return dict(EMPTY)
    return {k: value.get(k) for k in EMPTY}
