"""In-process pub/sub for SSE. Event types: message.new, conversation.updated, booking.updated, whatsapp.status."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_subscribers: set[asyncio.Queue] = set()


def publish(type_: str, data: dict) -> None:
    event = {"type": type_, "data": data}
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow consumer: drop


@asynccontextmanager
async def subscription(maxsize: int = 1000) -> AsyncIterator[asyncio.Queue]:
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _subscribers.add(q)
    try:
        yield q
    finally:
        _subscribers.discard(q)
