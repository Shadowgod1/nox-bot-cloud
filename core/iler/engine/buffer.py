"""Per-chat merge buffer (spec 5.1 step 3).

Each new client message restarts the chat's timer (`buffer_delay_seconds`). When the timer fires the
turn runs; a turn that is already running is never cancelled (it may be mid-send) — a message arriving
during it schedules one more turn that starts after it finishes. In-memory: single process (spec 5).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger("iler.engine.buffer")

SCALE = 1.0  # tests shrink the delay

_sleeping: dict[str, asyncio.Task] = {}
_running: dict[str, asyncio.Task] = {}
_all: set[asyncio.Task] = set()


def schedule(key: str, delay_seconds: float, job: Callable[[], Awaitable[None]]) -> asyncio.Task:
    """(Re)start the timer for `key`; `job` runs once the chat has been quiet for `delay_seconds`."""
    old = _sleeping.pop(key, None)
    if old is not None and not old.done():
        old.cancel()
    task = asyncio.create_task(_wait_and_run(key, delay_seconds * SCALE, job), name=f"buffer:{key}")
    _sleeping[key] = task
    _all.add(task)
    task.add_done_callback(_all.discard)
    return task


async def _wait_and_run(key: str, delay: float, job: Callable[[], Awaitable[None]]) -> None:
    me = asyncio.current_task()
    await asyncio.sleep(delay)  # cancellation here = merged into a later timer
    if _sleeping.get(key) is me:
        del _sleeping[key]
    while (prev := _running.get(key)) is not None and prev is not me:
        await asyncio.wait({prev})  # never raises; does not cancel `prev` if we are cancelled
    _running[key] = me
    try:
        await job()
    except Exception:  # noqa: BLE001 - the turn logs its own errors; never kill the loop
        log.exception("buffered job for %s failed", key)
    finally:
        if _running.get(key) is me:
            del _running[key]


def pending(key: str) -> bool:
    return key in _sleeping or key in _running


async def wait_idle() -> None:
    """Wait until no timers or turns are left (tests, shutdown)."""
    while _all:
        await asyncio.gather(*list(_all), return_exceptions=True)
