"""Tool plugin contract (spec section 5.3)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from iler.settings_models import AllSettings, BotSettings


@dataclass
class ToolContext:
    conversation_id: int | None  # None in test chat
    phone: str
    settings: BotSettings
    now: datetime
    dry_run: bool = False         # test chat: tools must not send WhatsApp messages or write bookings
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    content: str | dict
    user_visible: bool = False


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict  # JSON Schema (type: object)

    def enabled(self, settings: AllSettings) -> bool: ...

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult: ...
