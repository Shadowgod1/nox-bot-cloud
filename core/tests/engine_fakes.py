"""Shared fakes/fixtures for engine, sender, booking and builtin-tool tests (Tasks 5 and 7)."""

import itertools
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from iler import inbound, settings_store
from iler.engine import buffer
from iler.engine import handler as engine_handler
from iler.llm import router
from iler.llm.fake import FakeLLM
from iler.settings_models import BotSettings, NotifySettings
from iler.whatsapp import set_gateway
from iler.whatsapp.base import IncomingMessage

_ids = itertools.count(1)
TEST_PROMPT = "Ты — администратор тестового клуба «Тест». Адрес: ул. Абая 1."


class FakeGateway:
    """In-memory WhatsAppGateway: records sends and presence updates."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.presence: list[tuple[str, str, int]] = []
        self.fail: Exception | None = None
        self._n = 0

    def texts(self, jid: str | None = None) -> list[str]:
        return [t for j, t in self.sent if jid is None or j == jid]

    async def send_text(self, jid: str, text: str) -> str:
        if self.fail is not None:
            raise self.fail
        self._n += 1
        self.sent.append((jid, text))
        return f"OUT{self._n}"

    async def send_presence(self, jid, state, ms) -> None:
        self.presence.append((jid, state, ms))

    async def connection_state(self) -> str:
        return "open"

    async def connect(self) -> dict:
        return {}

    async def pairing_code(self, phone: str) -> str:
        return "CODE"

    async def logout(self) -> None:
        return None

    async def ensure_instance(self, webhook_url: str) -> None:
        return None


CLIENT_PHONE = "77010000001"
CLIENT_JID = f"{CLIENT_PHONE}@s.whatsapp.net"
ADMIN_PHONE = "77010000099"
ADMIN_JID = f"{ADMIN_PHONE}@s.whatsapp.net"
ADMIN_GROUP = "120363000000000001@g.us"


def make_msg(text: str, *, phone: str = CLIENT_PHONE, jid: str | None = None, from_me: bool = False,
             is_group: bool = False, evo_id: str | None = None, push_name: str = "Айдар") -> IncomingMessage:
    return IncomingMessage(
        evo_id=evo_id or f"EVO{next(_ids)}",
        jid=jid or f"{phone}@s.whatsapp.net",
        phone=phone,
        push_name=push_name,
        text=text,
        from_me=from_me,
        is_group=is_group,
        media=None,
        ts=datetime.now(UTC),
    )


@pytest.fixture
async def env(pool, monkeypatch):
    """Fake WhatsApp gateway + FakeLLM + fast merge buffer; waits for background turns on teardown."""
    gw = FakeGateway()
    set_gateway(gw)
    holder = SimpleNamespace(gw=gw, llm=FakeLLM(default="Здравствуйте! Чем помочь?"))
    router.set_provider_factory(lambda name, s: holder.llm)
    monkeypatch.setattr(buffer, "SCALE", 0.02)
    prev_inbound = inbound.get_handler()
    prev_admin = engine_handler.get_admin_handler()
    from iler.bookings import admin_reply

    admin_reply.install()

    async def set_bot(**kw):
        kw.setdefault("prompt", TEST_PROMPT)  # an empty prompt means "not configured": the bot stays silent
        await settings_store.put("bot", BotSettings(**kw))

    async def set_notify(**kw):
        await settings_store.put("notify", NotifySettings(**kw))

    async def idle():
        await inbound.wait_idle()
        await buffer.wait_idle()

    holder.set_bot = set_bot
    await set_bot()
    holder.set_notify = set_notify
    holder.idle = idle
    yield holder
    await idle()
    router.set_provider_factory(None)
    set_gateway(None)
    inbound.set_handler(prev_inbound)
    engine_handler.set_admin_handler(prev_admin)
