"""WhatsApp gateway contract and the parsed inbound message type."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass
class IncomingMessage:
    evo_id: str
    jid: str            # e.g. 77010000000@s.whatsapp.net or ...@g.us
    phone: str          # digits only
    push_name: str
    text: str
    from_me: bool
    is_group: bool
    media: dict | None
    ts: datetime


class WhatsAppGateway(Protocol):
    async def send_text(self, jid: str, text: str) -> str: ...  # returns evo message id

    async def send_presence(self, jid: str, state: Literal["composing", "paused"], ms: int) -> None: ...

    async def connection_state(self) -> str: ...  # close | connecting | open

    async def connect(self) -> dict: ...  # {"qr_base64": "..."} when a QR is available

    async def pairing_code(self, phone: str) -> str: ...

    async def logout(self) -> None: ...

    async def ensure_instance(self, webhook_url: str) -> None: ...
