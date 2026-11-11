"""Outbound reconcile: intents stuck in `sending` (crash mid-send) become `uncertain` (never auto-resent:
the message may have gone out); fresh `failed` intents, and reply parts left `pending` after an earlier part
failed, are retried a few times (`sender.retry_intent` drops outdated bot replies as `failed`/`stale`).

The same loop runs the engine's unanswered-message sweeper every SWEEP_INTERVAL_SECONDS."""

import asyncio
import logging
import time

from iler.db.pool import get_pool
from iler.engine import handler, sender

log = logging.getLogger("iler.workers.outbound_reconcile")

INTERVAL_SECONDS = 30
SWEEP_INTERVAL_SECONDS = 60
STUCK_SENDING_MINUTES = 2
RETRY_AFTER_SECONDS = 30
RETRY_WINDOW_MINUTES = 10   # older failures are stale: a late reply would confuse the client
MAX_ATTEMPTS = 3


async def reconcile_once() -> int:
    """Returns the number of intents re-sent."""
    pool = get_pool()
    await pool.execute(
        "UPDATE outbound_intents SET status = 'uncertain', error = coalesce(error, 'interrupted while sending'),"
        " updated_at = now()"
        f" WHERE status = 'sending' AND updated_at < now() - interval '{STUCK_SENDING_MINUTES} minutes'"
    )
    await pool.execute(
        f"UPDATE outbound_intents SET status = 'failed', error = '{sender.STALE}', updated_at = now()"
        f" WHERE status = 'pending' AND created_at < now() - interval '{RETRY_WINDOW_MINUTES} minutes'"
    )
    rows = await pool.fetch(
        "SELECT id FROM outbound_intents WHERE status IN ('pending', 'failed') AND attempts < $1"
        " AND error IS DISTINCT FROM $2"
        f" AND updated_at < now() - interval '{RETRY_AFTER_SECONDS} seconds'"
        f" AND created_at > now() - interval '{RETRY_WINDOW_MINUTES} minutes'"
        " ORDER BY id LIMIT 50",
        MAX_ATTEMPTS, sender.STALE,
    )
    retried = 0
    for r in rows:
        if await sender.retry_intent(r["id"]) is not None:
            retried += 1
    return retried


async def run(app) -> None:
    last_sweep = time.monotonic()
    while True:
        try:
            await reconcile_once()
        except Exception:  # noqa: BLE001
            log.exception("outbound reconcile tick failed")
        if time.monotonic() - last_sweep >= SWEEP_INTERVAL_SECONDS:
            last_sweep = time.monotonic()
            try:
                await handler.sweep_unanswered()
            except Exception:  # noqa: BLE001
                log.exception("unanswered-message sweep failed")
        await asyncio.sleep(INTERVAL_SECONDS)
