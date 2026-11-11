"""Re-dispatches inbound events left in `received` (handler error, restart mid-processing)."""

import asyncio
import logging

from iler import inbound
from iler.db.pool import get_pool

log = logging.getLogger("iler.workers.inbound_retry")

INTERVAL_SECONDS = 10
RETRY_AFTER_SECONDS = 30      # fresh events belong to the webhook task
STALE_PROCESSING_MINUTES = 10  # a handler may legitimately wait for the merge buffer and the LLM
BATCH = 50


async def recover_after_restart() -> int:
    """Single process: anything `processing` at startup was interrupted by the previous run."""
    result = await get_pool().execute(
        "UPDATE inbound_events SET status = 'received', updated_at = now() WHERE status = 'processing'"
    )
    return int(result.split()[-1])


async def retry_once() -> int:
    pool = get_pool()
    await pool.execute(
        "UPDATE inbound_events SET status = 'received', updated_at = now()"
        f" WHERE status = 'processing' AND updated_at < now() - interval '{STALE_PROCESSING_MINUTES} minutes'"
    )
    rows = await pool.fetch(
        "SELECT id, payload, created_at FROM inbound_events"
        f" WHERE status = 'received' AND updated_at < now() - interval '{RETRY_AFTER_SECONDS} seconds'"
        " ORDER BY id LIMIT $1",
        BATCH,
    )
    if rows and inbound.get_handler() is None:
        return 0  # engine not registered: keep events for later
    for row in rows:
        await inbound.dispatch(row)  # sequential: keeps per-chat order
    return len(rows)


async def run(app) -> None:
    while True:
        try:
            await recover_after_restart()
            break
        except Exception:  # noqa: BLE001
            log.exception("inbound recovery failed; retrying")
            await asyncio.sleep(INTERVAL_SECONDS)
    while True:
        try:
            await retry_once()
        except Exception:  # noqa: BLE001
            log.exception("inbound retry tick failed")
        await asyncio.sleep(INTERVAL_SECONDS)
