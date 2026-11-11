"""Server-Sent Events stream (spec section 7): message.new, conversation.updated, booking.updated, whatsapp.status.

Frames are `event: <type>\\ndata: <json>\\n\\n`; a `: ping` comment is sent every HEARTBEAT_SECONDS so
proxies keep the connection open. The stream ends as soon as the client disconnects.
"""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from iler import events
from iler.auth.deps import User, current_user

router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 20.0
# How often the loop wakes up to check for a disconnected client.
DISCONNECT_POLL_SECONDS = 1.0


def format_event(type_: str, data: dict) -> str:
    return f"event: {type_}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def event_stream(request: Request) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    async with events.subscription() as q:
        yield ": connected\n\n"
        last_beat = loop.time()
        while True:
            if await request.is_disconnected():
                return
            timeout = max(0.0, min(DISCONNECT_POLL_SECONDS, HEARTBEAT_SECONDS - (loop.time() - last_beat)))
            try:
                event = await asyncio.wait_for(q.get(), timeout=timeout)
            except TimeoutError:
                if loop.time() - last_beat >= HEARTBEAT_SECONDS:
                    last_beat = loop.time()
                    yield ": ping\n\n"
                continue
            yield format_event(event["type"], event["data"])


@router.get("/api/events", response_class=StreamingResponse)
async def get_events(request: Request, _user: User = Depends(current_user)):
    return StreamingResponse(
        event_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
