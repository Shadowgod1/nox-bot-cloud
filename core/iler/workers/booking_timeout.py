"""Booking timeout: a pending request older than `booking_request_timeout_min` gets one follow-up to the
client ("администратор скоро свяжется") and the `timeout_notified` flag for the admin panel."""

import asyncio
import logging

from iler import settings_store
from iler.bookings import service
from iler.settings_models import BotSettings

log = logging.getLogger("iler.workers.booking_timeout")

INTERVAL_SECONDS = 60


async def check_once() -> int:
    bot = await settings_store.get("bot", BotSettings)
    return await service.notify_timeouts(bot.booking_request_timeout_min, bot.booking_timeout_message)


async def run(app) -> None:
    while True:
        try:
            await check_once()
        except Exception:  # noqa: BLE001
            log.exception("booking timeout tick failed")
        await asyncio.sleep(INTERVAL_SECONDS)
