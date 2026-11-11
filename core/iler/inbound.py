"""Inbound pipeline: stored webhook events (`inbound_events`) -> filters -> registered handler.

Lifecycle of an event row: received -> processing -> processed | ignored | failed.
- The webhook stores the event and calls `schedule(row)`; the retry worker re-dispatches rows that
  stayed `received` (handler error, no handler yet, process restart).
- `dispatch` claims a row atomically (`received` -> `processing`), so the webhook task and the
  retry worker never hand the same event to the handler twice.
- A handler exception returns the row to `received` until MAX_ATTEMPTS, then `failed`.

The engine (Task 5) registers itself with `set_handler`. Messages with `from_me` are passed on:
the engine tells bot echoes from operator replies.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from iler import settings_store
from iler.db.pool import get_pool
from iler.settings_models import NotifySettings
from iler.whatsapp import monitor
from iler.whatsapp.base import IncomingMessage
from iler.whatsapp.parse import parse_upsert

log = logging.getLogger("iler.inbound")

MAX_ATTEMPTS = 5

Handler = Callable[[IncomingMessage], Awaitable[None]]
_handler: Handler | None = None
_tasks: set[asyncio.Task] = set()


def set_handler(fn: Handler | None) -> None:
    global _handler
    _handler = fn


def get_handler() -> Handler | None:
    return _handler


async def record_send_failure(error: str) -> None:
    """Count a failed WhatsApp send (EvolutionGateway.send_text already calls this)."""
    await monitor.record_send_failure(error)


async def record_send_success() -> None:
    """Reset the failure counter (EvolutionGateway.send_text already calls this)."""
    await monitor.record_send_success()


async def store_event(evo_message_id: str, payload: dict):
    """Insert an event; returns the new row, or None for a duplicate evo id."""
    return await get_pool().fetchrow(
        "INSERT INTO inbound_events(evo_message_id, payload) VALUES ($1, $2)"
        " ON CONFLICT (evo_message_id) DO NOTHING RETURNING id, payload, created_at",
        evo_message_id,
        payload,
    )


def schedule(event_row) -> asyncio.Task:
    """Dispatch in the background; the task is tracked so it is not garbage-collected."""
    task = asyncio.create_task(dispatch(event_row))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def wait_idle() -> None:
    """Wait for scheduled dispatches (tests, shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def _should_ignore(msg: IncomingMessage | None) -> bool:
    if msg is None:
        return True  # status/broadcast, service message, unparseable
    if msg.is_group:
        notify = await settings_store.get("notify", NotifySettings)
        return not notify.admin_group_jid or msg.jid != notify.admin_group_jid
    return False


def _field(row, name):
    return row[name] if not isinstance(row, dict) else row.get(name)


async def dispatch(event_row) -> None:
    """Process one stored event (record or dict with id, payload, created_at). Never raises."""
    event_id = _field(event_row, "id")
    try:
        payload = _field(event_row, "payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        received_at = _field(event_row, "created_at") or datetime.now(UTC)
        msg = parse_upsert(payload or {}, received_at=received_at)
        pool = get_pool()

        if await _should_ignore(msg):
            await pool.execute(
                "UPDATE inbound_events SET status = 'ignored', updated_at = now() WHERE id = $1 AND status = 'received'",
                event_id,
            )
            return

        handler = _handler
        if handler is None:
            log.warning("no inbound handler registered; event %s stays received", event_id)
            return

        attempts = await pool.fetchval(
            "UPDATE inbound_events SET status = 'processing', attempts = attempts + 1, updated_at = now()"
            " WHERE id = $1 AND status = 'received' RETURNING attempts",
            event_id,
        )
        if attempts is None:
            return  # already claimed elsewhere or finished

        try:
            await handler(msg)
        except Exception as e:  # noqa: BLE001
            log.exception("inbound handler failed for event %s", event_id)
            status = "failed" if attempts >= MAX_ATTEMPTS else "received"
            await pool.execute(
                "UPDATE inbound_events SET status = $2, error = $3, updated_at = now() WHERE id = $1",
                event_id, status, f"{type(e).__name__}: {e}"[:1000],
            )
            return
        await pool.execute(
            "UPDATE inbound_events SET status = 'processed', error = NULL, updated_at = now() WHERE id = $1",
            event_id,
        )
    except Exception:  # noqa: BLE001 - background task: log, the retry worker picks the row up again
        log.exception("dispatch of inbound event %s failed", event_id)
