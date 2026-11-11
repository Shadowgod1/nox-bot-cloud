"""Daily backup at 04:00 (club time zone) with 14-day retention. Loops forever, survives errors."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from iler.backup import run as run_mod
from iler.backup.schedule import next_run
from iler.config import get_settings

log = logging.getLogger("iler.workers.backup")

MAX_SLEEP = 300  # re-check the clock regularly (host suspend, clock changes)


async def _sleep_until(target: datetime) -> None:
    while True:
        remaining = (target - datetime.now(target.tzinfo)).total_seconds()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, MAX_SLEEP))


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().tz)
    except Exception:  # noqa: BLE001 - a bad ILER_TZ must not stop backups
        log.exception("invalid ILER_TZ, falling back to UTC")
        return ZoneInfo("UTC")


async def run(app) -> None:
    while True:
        tz = _tz()
        await _sleep_until(next_run(datetime.now(tz), tz))
        try:
            info = await run_mod.create_backup()
            log.info("scheduled backup done: %s (%d bytes)", info.name, info.size)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("scheduled backup failed")
            continue  # keep existing backups: no retention after a failed run
        try:
            run_mod.apply_retention()
        except Exception:  # noqa: BLE001
            log.exception("backup retention failed")
