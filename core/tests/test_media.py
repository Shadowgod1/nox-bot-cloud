"""Voice messages and photos: fixed reply instead of an LLM guess, operator handover by the settings."""

import dataclasses

import pytest
from engine_fakes import ADMIN_PHONE, CLIENT_JID, env, make_msg  # noqa: F401
from httpx import AsyncClient

from iler import settings_store
from iler.engine import handler, media
from iler.settings_models import PHOTO_MESSAGE_DEFAULT, VOICE_MESSAGE_DEFAULT, BotSettings

OWNER = {"email": "owner@salon.kz", "password": "owner-pass-123", "name": "Owner"}


def _msg(text: str, kind: str | None):
    return dataclasses.replace(make_msg(text), media={"type": kind, "mimetype": "audio/ogg"} if kind else None)


async def _conv(pool):
    return await pool.fetchrow("SELECT * FROM conversations WHERE chat_jid = $1", CLIENT_JID)


async def test_voice_without_text_asks_to_write_without_llm(env, pool):
    await handler.handle_incoming(_msg("", "audio"))
    await env.idle()
    assert env.gw.texts(CLIENT_JID) == [VOICE_MESSAGE_DEFAULT]
    assert env.llm.calls == []
    assert (await _conv(pool))["status"] == "active"


async def test_voice_in_kazakh_conversation_gets_kazakh_reply(env, pool):
    await handler.handle_incoming(make_msg("Сәлеметсіз бе!"))
    await env.idle()
    await handler.handle_incoming(_msg("", "audio"))
    await env.idle()
    assert env.gw.texts(CLIENT_JID)[-1] == media._KK["voice"]


async def test_voice_handover_mode_passes_to_operator(env, pool):
    await env.set_bot(voice_mode="handover")
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    await handler.handle_incoming(_msg("", "audio"))
    await env.idle()
    conv = await _conv(pool)
    assert conv["status"] == "needs_operator" and conv["paused_until"] is not None
    client_texts = env.gw.texts(CLIENT_JID)
    assert client_texts == [media.VOICE_HANDOVER_DEFAULT]
    admin = [t for j, t in env.gw.sent if j != CLIENT_JID]
    assert len(admin) == 1 and "голосовое" in admin[0]
    assert env.llm.calls == []


async def test_photo_with_caption_thanks_and_hands_over(env, pool):
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    await handler.handle_incoming(_msg("хочу такой дизайн", "image"))
    await env.idle()
    assert env.gw.texts(CLIENT_JID) == [PHOTO_MESSAGE_DEFAULT]
    assert (await _conv(pool))["status"] == "needs_operator"
    assert any("фото" in t for j, t in env.gw.sent if j != CLIENT_JID)
    assert env.llm.calls == []


async def test_photo_reply_mode_and_custom_text(env, pool):
    await env.set_bot(photo_mode="reply", photo_message="Красиво! Мастер посмотрит и напишет цену.")
    await handler.handle_incoming(_msg("", "image"))
    await env.idle()
    assert env.gw.texts(CLIENT_JID) == ["Красиво! Мастер посмотрит и напишет цену."]
    assert (await _conv(pool))["status"] == "active"


async def test_voice_with_text_goes_to_llm_with_label(env, pool):
    await handler.handle_incoming(_msg("сколько стоит маникюр?", "audio"))
    await env.idle()
    assert len(env.llm.calls) == 1
    user = [m.content for m in env.llm.calls[0]["messages"] if m.role == "user"]
    assert "[голосовое сообщение] сколько стоит маникюр?" in user[-1]


def test_decide_rules():
    bot = BotSettings(prompt="x")
    assert media.decide([], bot) is None
    assert media.decide([("привет", None)], bot) is None
    assert media.decide([("", {"type": "sticker"})], bot) is None
    assert media.decide([("", {"type": "audio"})], bot).kind == "voice"
    d = media.decide([("", {"type": "audio"}), ("", {"type": "video"})], bot)
    assert d.kind == "photo" and d.handover
    d = media.decide([("", {"type": "image"})], bot, ["Сәлем"])
    assert d.reply == media._KK["photo"]
    custom = BotSettings(prompt="x", voice_message="Пишите текстом, пожалуйста")
    assert media.decide([("", {"type": "audio"})], custom, ["Сәлем"]).reply == "Пишите текстом, пожалуйста"


# --- test chat ---------------------------------------------------------------------------------------

def csrf(c: AsyncClient) -> dict:
    return {"X-ILER-CSRF": c.cookies.get("iler_csrf", "")}


@pytest.fixture
async def owner(client):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    return client


async def test_test_chat_simulates_voice_and_photo(env, owner):
    r = await owner.post("/api/test-chat", headers=csrf(owner),
                         json={"messages": [{"role": "user", "content": "", "media": "audio"}]})
    assert r.status_code == 200, r.text
    assert r.json() == {"reply": VOICE_MESSAGE_DEFAULT, "tool_calls": [], "handed_over": False, "usage": None}
    r = await owner.post("/api/test-chat", headers=csrf(owner),
                         json={"messages": [{"role": "user", "content": "вот такой", "media": "image"}]})
    assert r.json()["reply"] == PHOTO_MESSAGE_DEFAULT and r.json()["handed_over"] is True
    assert env.llm.calls == [] and env.gw.sent == []
    r = await owner.post("/api/test-chat", headers=csrf(owner), json={"messages": [{"role": "user", "content": ""}]})
    assert r.status_code == 422


async def test_test_chat_reports_coins_from_usage(env, owner):
    from iler.llm.base import LlmResponse
    from iler.llm.fake import FakeLLM

    env.llm = FakeLLM(default=LlmResponse(text="Маникюр от 6000 ₸", model="claude-haiku-5-5",
                                          input_tokens=10_000, output_tokens=2_000))
    r = await owner.post("/api/test-chat", headers=csrf(owner),
                         json={"messages": [{"role": "user", "content": "Сколько стоит маникюр?"}]})
    assert r.status_code == 200, r.text
    # haiku: (10k * $0.10 + 2k * $0.50) / 1M = $0.002 = 2 coins
    assert r.json()["usage"] == {"coins": 2.0, "input_tokens": 10_000, "output_tokens": 2_000}


async def test_settings_roundtrip_knowledge(owner):
    body = (await owner.get("/api/settings/bot")).json()
    body |= {
        "services": [{"name": "Маникюр", "price": "от 5000 ₸", "duration_min": 60}],
        "staff": [{"name": "Айгерим", "services": ["Маникюр"], "thu": {"works": True, "open": "10:00", "close": "19:00"},
                   "no_online_booking": False}],
        "voice_mode": "handover",
    }
    r = await owner.put("/api/settings/bot", json=body, headers=csrf(owner))
    assert r.status_code == 200, r.text
    stored = await settings_store.get("bot", BotSettings)
    assert stored.staff[0].thu.works and stored.services[0].duration_min == 60 and stored.voice_mode == "handover"
    bad = body | {"staff": [{"name": "X", "mon": {"works": True, "open": "25:00", "close": "19:00"}}]}
    assert (await owner.put("/api/settings/bot", json=bad, headers=csrf(owner))).status_code == 422
