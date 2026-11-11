"""WhatsApp connection monitor: makes sure the instance and its webhook exist, then polls the state.

Never deletes or recreates an instance (spec 11.2); a missing one is created, an existing one is reused.
"""

import asyncio
import logging

from iler.config import get_settings
from iler.whatsapp import get_gateway, monitor

log = logging.getLogger("iler.workers.wa_monitor")

INTERVAL_SECONDS = 60


async def run(app) -> None:
    ensured = False
    while True:
        gateway = get_gateway()
        if not ensured:
            try:
                await gateway.ensure_instance(get_settings().webhook_url)
                ensured = True
            except Exception as e:  # noqa: BLE001
                log.warning("ensure_instance failed (will retry): %s", e)
        try:
            await monitor.check_once(gateway)
        except Exception:  # noqa: BLE001
            log.exception("connection check failed")
        await asyncio.sleep(INTERVAL_SECONDS)
