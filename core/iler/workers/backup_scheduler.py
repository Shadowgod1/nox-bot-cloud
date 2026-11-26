"""Daily backup at 04:00 (club time zone) with 14-day retention. Loops forever, survives errors."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from iler.backup import run as run_mod
from iler.backup import status as backup_status
from iler.backup.schedule import next_run
from iler.config import brand_name, get_settings
from iler.whatsapp import monitor

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


async def _report_failure(message: str) -> None:
    """Remember the failure (shown on the dashboard) and e-mail the admin if SMTP is configured."""
    await backup_status.safe_record(False, message)
    try:
        if await monitor.smtp_configured():
            await monitor.send_alert(f"Scheduled backup failed: {message}"[:1000], subject=f"{brand_name()} Bot: backup failed")
    except Exception:  # noqa: BLE001
        log.exception("backup failure alert delivery failed")


async def run(app) -> None:
    while True:
        tz = _tz()
        await _sleep_until(next_run(datetime.now(tz), tz))
        try:
            info = await run_mod.create_backup()
            log.info("scheduled backup done: %s (%d bytes)", info.name, info.size)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("scheduled backup failed")
            await _report_failure(str(e) or type(e).__name__)
            continue  # keep existing backups: no retention after a failed run
        await backup_status.safe_record(True, info.name)
        try:
            run_mod.apply_retention()
        except Exception:  # noqa: BLE001
            log.exception("backup retention failed")
