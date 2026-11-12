"""Conversation engine (Task 5): buffer, admission, prompt, turn, handler."""

from datetime import UTC, datetime, timedelta

import pytest
from engine_fakes import ADMIN_GROUP, ADMIN_JID, ADMIN_PHONE, CLIENT_JID, env, make_msg  # noqa: F401

from iler import events, inbound
from iler.engine import admission, handler, turn
from iler.llm.base import ChatMessage, LlmError, LlmResponse
from iler.llm.fake import FakeLLM
from iler.settings_models import DayHours, NotifySettings, WeeklyHours


def _user_texts(call) -> list[str]:
    return [m.content for m in call["messages"] if m.role == "user"]


async def _conv(pool, jid=CLIENT_JID):
    return await pool.fetchrow("SELECT * FROM conversations WHERE chat_jid = $1", jid)


async def test_burst_merged_into_one_turn(env, pool):
    for text in ("Привет", "хочу забронировать", "на вечер"):
        await handler.handle_incoming(make_msg(text))
    await env.idle()
    assert len(env.llm.calls) == 1
    joined = "\n".join(_user_texts(env.llm.calls[0]))
    for text in ("Привет", "хочу забронировать", "на вечер"):
        assert text in joined
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте! Чем помочь?"]
    rows = await pool.fetch("SELECT author FROM messages ORDER BY id")
    assert [r["author"] for r in rows] == ["client", "client", "client", "bot"]


def _upsert(mid: str, text: str) -> dict:
    return {
        "event": "messages.upsert",
        "instance": "iler",
        "data": {
            "key": {"remoteJid": CLIENT_JID, "fromMe": False, "id": mid},
            "pushName": "Айдар",
            "message": {"conversation": text},
        },
    }


async def test_duplicate_webhook_and_burst_yield_single_reply(env, client, pool):
    inbound.set_handler(handler.handle_incoming)
    headers = {"apikey": "test-evo-key"}
    for mid, text in (("M1", "Привет"), ("M2", "есть места?"), ("M1", "Привет")):
        r = await client.post("/internal/evolution/webhook", json=_upsert(mid, text), headers=headers)
        assert r.status_code == 200
    await env.idle()
    await env.idle()
    assert len(env.llm.calls) == 1
    assert len(env.gw.texts(CLIENT_JID)) == 1
    assert await pool.fetchval("SELECT count(*) FROM messages WHERE author = 'client'") == 2
    # a re-dispatch of an already processed event does not produce another reply
    row = await pool.fetchrow("SELECT id, payload, created_at FROM inbound_events WHERE evo_message_id = 'M1'")
    await pool.execute("UPDATE inbound_events SET status = 'received' WHERE id = $1", row["id"])
    await inbound.dispatch(row)
    await env.idle()
    assert len(env.gw.texts(CLIENT_JID)) == 1


async def test_paused_conversation_no_reply(env, pool):
    await pool.execute(
        "INSERT INTO conversations(chat_jid, phone, status, paused_until) VALUES ($1, '77010000001', 'paused', $2)",
        CLIENT_JID, datetime.now(UTC) + timedelta(hours=1),
    )
    await handler.handle_incoming(make_msg("Алло"))
    await env.idle()
    assert env.llm.calls == []
    assert env.gw.sent == []
    assert await pool.fetchval("SELECT count(*) FROM messages") == 1


async def test_expired_pause_resumes_bot(env, pool):
    await pool.execute(
        "INSERT INTO conversations(chat_jid, phone, status, paused_until) VALUES ($1, '77010000001', 'paused', $2)",
        CLIENT_JID, datetime.now(UTC) - timedelta(minutes=1),
    )
    await handler.handle_incoming(make_msg("Алло"))
    await env.idle()
    assert len(env.gw.texts(CLIENT_JID)) == 1
    assert (await _conv(pool))["status"] == "active"


async def test_operator_fromme_pauses_for_handover_minutes(env, pool):
    await env.set_bot(handover_pause_minutes=30)
    await handler.handle_incoming(make_msg("Сейчас подскажу", from_me=True))
    conv = await _conv(pool)
    assert conv["status"] == "paused"
    delta = conv["paused_until"] - datetime.now(UTC)
    assert timedelta(minutes=29) < delta <= timedelta(minutes=30, seconds=5)
    assert await pool.fetchval("SELECT author FROM messages") == "operator"
    await handler.handle_incoming(make_msg("Спасибо"))
    await env.idle()
    assert env.llm.calls == []
    assert env.gw.sent == []


async def test_bot_echo_fromme_is_not_operator(env, pool):
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    sent_text = env.gw.texts(CLIENT_JID)[0]
    await handler.handle_incoming(make_msg(sent_text, from_me=True, evo_id="OUT1"))
    await handler.handle_incoming(make_msg(sent_text, from_me=True, evo_id="SOMEOTHERID"))
    conv = await _conv(pool)
    assert conv["status"] == "active"
    assert await pool.fetchval("SELECT count(*) FROM messages WHERE author = 'operator'") == 0


def _closed_hours() -> WeeklyHours:
    closed = DayHours(closed=True)
    return WeeklyHours(always_open=False, mon=closed, tue=closed, wed=closed, thu=closed, fri=closed,
                       sat=closed, sun=closed)


@pytest.mark.parametrize("mode", ["reply", "silent", "fixed_message"])
async def test_after_hours_modes(env, mode):
    await env.set_bot(hours=_closed_hours(), after_hours_mode=mode, after_hours_message="Мы закрыты, ответим утром")
    await handler.handle_incoming(make_msg("Вы работаете?"))
    await env.idle()
    if mode == "reply":
        assert len(env.llm.calls) == 1
        assert env.gw.texts(CLIENT_JID) == ["Здравствуйте! Чем помочь?"]
    elif mode == "silent":
        assert env.llm.calls == []
        assert env.gw.sent == []
    else:
        assert env.llm.calls == []
        assert env.gw.texts(CLIENT_JID) == ["Мы закрыты, ответим утром"]


def test_within_hours():
    hours = WeeklyHours(always_open=False, wed=DayHours(open="10:00", close="02:00"))
    wed_noon = datetime(2026, 11, 11, 12, 0)  # Wednesday
    assert admission.within_hours(hours, wed_noon)
    assert not admission.within_hours(hours, datetime(2026, 11, 11, 9, 0))
    assert admission.within_hours(hours, datetime(2026, 11, 12, 1, 30))  # after midnight, Wednesday's shift
    hours.thu = DayHours(closed=True)
    assert admission.within_hours(hours, datetime(2026, 11, 12, 1, 30))
    assert not admission.within_hours(hours, datetime(2026, 11, 12, 12, 0))


async def test_prompt_contains_tz_now_and_business_prompt(env):
    await env.set_bot(prompt="Ты — администратор клуба. VIP-зал — 1500 ₸/час. Адрес: ул. Абая 1.")
    await handler.handle_incoming(make_msg("Сколько стоит?"))
    await env.idle()
    system = env.llm.calls[0]["messages"][0]
    assert system.role == "system"
    assert "VIP-зал" in system.content and "1500 ₸/час" in system.content
    assert "ул. Абая 1" in system.content
    assert "Asia/Almaty" in system.content
    from zoneinfo import ZoneInfo

    assert datetime.now(ZoneInfo("Asia/Almaty")).strftime("%d.%m.%Y") in system.content
    # the WhatsApp push name is client-controlled: it goes to the user side as quoted data, never the system prompt
    assert "Айдар" not in system.content
    first_user = env.llm.calls[0]["messages"][1]
    assert first_user.role == "user" and "«Айдар»" in first_user.content and "Сколько стоит?" in first_user.content


async def test_history_limited_to_memory_messages(env, pool):
    await env.set_bot(memory_messages=4)
    cid = await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone) VALUES ($1, '77010000001') RETURNING id", CLIENT_JID
    )
    for i in range(10):
        author, direction = ("client", "in") if i % 2 == 0 else ("bot", "out")
        await pool.execute(
            "INSERT INTO messages(conversation_id, direction, author, text) VALUES ($1, $2, $3, $4)",
            cid, direction, author, f"msg-{i}",
        )
    await pool.execute(
        "INSERT INTO messages(conversation_id, direction, author, text) VALUES ($1, 'in', 'client', 'last')", cid
    )
    out = await turn.run_turn(cid)
    assert out.reply_parts == ["Здравствуйте! Чем помочь?"]
    contents = [m.content for m in env.llm.calls[0]["messages"][1:]]
    assert contents[-1] == "last"
    assert sum(1 for m in env.llm.calls[0]["messages"][1:]) <= 4
    assert not any("msg-0" in c or "msg-5" in c for c in contents)
    assert any("msg-9" in c for c in contents)


class PlaceholderEcho(FakeLLM):
    """Replies with the masked token it found in the last user message."""

    async def complete(self, messages, tools, model):
        self.calls.append({"messages": list(messages), "tools": list(tools), "model": model})
        import re

        token = re.search(r"\[PHONE_\d+\]", messages[-1].content).group(0)
        return LlmResponse(text=f"Записал номер {token}")


async def test_pii_masked_before_llm_and_restored(env):
    env.llm = PlaceholderEcho()
    await handler.handle_incoming(make_msg("Мой номер +7 701 123 45 67, перезвоните"))
    await env.idle()
    for call in env.llm.calls:
        for m in call["messages"]:
            assert "701 123 45 67" not in m.content
    assert env.gw.texts(CLIENT_JID) == ["Записал номер +7 701 123 45 67"]


async def test_split_marker_sends_multiple_parts_with_presence(env):
    env.llm = FakeLLM(default="Здравствуйте![SPLIT]Свободно 5 мест в общем зале.")
    await handler.handle_incoming(make_msg("Есть места?"))
    await env.idle()
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте!", "Свободно 5 мест в общем зале."]
    assert [(p[1], p[2]) for p in env.gw.presence] == [
        ("composing", len("Здравствуйте!") * 40),
        ("composing", min(len("Свободно 5 мест в общем зале.") * 40, 4000)),
    ]


async def test_admin_messages_routed_to_admin_handler(env, pool):
    await env.set_notify(admin_phones=["+7 (701) 000-00-99"], admin_group_jid=ADMIN_GROUP)
    seen = []

    async def admin_fn(msg):
        seen.append(msg.text)

    handler.set_admin_handler(admin_fn)
    await handler.handle_incoming(make_msg("+1", phone=ADMIN_PHONE))
    await handler.handle_incoming(make_msg("-2 нет мест", jid=ADMIN_GROUP, phone="77010000055", is_group=True))
    await env.idle()
    assert seen == ["+1", "-2 нет мест"]
    assert env.llm.calls == []
    assert await pool.fetchval("SELECT count(*) FROM conversations") == 0


def test_is_admin():
    notify = NotifySettings(admin_phones=["+7 701 000 00 99"], admin_group_jid=ADMIN_GROUP)
    assert admission.is_admin(ADMIN_JID, notify)
    assert admission.is_admin("77010000099", notify)
    assert admission.is_admin(ADMIN_GROUP, notify)
    assert not admission.is_admin(CLIENT_JID, notify)
    assert not admission.is_admin("", notify)


async def test_bot_disabled_no_reply(env, pool):
    await env.set_bot(enabled=False)
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    assert env.llm.calls == []
    assert env.gw.sent == []
    assert await pool.fetchval("SELECT count(*) FROM messages") == 1


async def test_llm_failure_sets_needs_operator(env, pool):
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    env.llm = FakeLLM(script=[LlmError("provider down", retryable=False)])
    async with events.subscription() as q:
        await handler.handle_incoming(make_msg("Привет"))
        await env.idle()
        got = []
        while not q.empty():
            got.append(q.get_nowait())
    conv = await _conv(pool)
    assert conv["status"] == "needs_operator"
    assert env.gw.texts(CLIENT_JID) == []
    assert len(env.gw.texts(ADMIN_JID)) == 1
    updates = [e for e in got if e["type"] == "conversation.updated"]
    assert updates and updates[-1]["data"]["conversation"]["status"] == "needs_operator"
    # the next client message waits for the operator
    await handler.handle_incoming(make_msg("Ау"))
    await env.idle()
    assert env.gw.texts(CLIENT_JID) == []


async def test_startup_recovers_unanswered_message(env, pool):
    now = datetime.now(UTC)
    fresh = await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone, last_message_at) VALUES ($1, '77010000001', $2) RETURNING id",
        CLIENT_JID, now,
    )
    await pool.execute(
        "INSERT INTO messages(conversation_id, direction, author, text, created_at)"
        " VALUES ($1, 'in', 'client', 'Есть места?', $2)", fresh, now - timedelta(minutes=2),
    )
    old = await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone) VALUES ('77010000002@s.whatsapp.net', '77010000002') RETURNING id"
    )
    await pool.execute(
        "INSERT INTO messages(conversation_id, direction, author, text, created_at)"
        " VALUES ($1, 'in', 'client', 'старое', $2)", old, now - timedelta(minutes=30),
    )
    answered = await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone) VALUES ('77010000003@s.whatsapp.net', '77010000003') RETURNING id"
    )
    await pool.execute(
        "INSERT INTO messages(conversation_id, direction, author, text, created_at) VALUES"
        " ($1, 'in', 'client', 'вопрос', $2), ($1, 'out', 'bot', 'ответ', $3)",
        answered, now - timedelta(minutes=3), now - timedelta(minutes=2),
    )
    assert await handler.recover_unanswered() == 1
    await env.idle()
    assert [j for j, _ in env.gw.sent] == [CLIENT_JID]


async def test_dry_run_uses_history_and_writes_nothing(env, pool):
    out = await turn.run_turn(
        None, dry_run=True, history=[ChatMessage(role="user", content="Привет")], phone="77010000001"
    )
    assert out.reply_parts == ["Здравствуйте! Чем помочь?"]
    assert out.handed_over is False
    assert env.gw.sent == []
    assert await pool.fetchval("SELECT count(*) FROM messages") == 0
    assert _user_texts(env.llm.calls[0]) == ["Привет"]


# --- review fixes -------------------------------------------------------------------------------------


class _SecondMessageDuringTurn(FakeLLM):
    """First call: the client writes again while the LLM is "thinking"."""

    async def complete(self, messages, tools, model):
        self.calls.append({"messages": list(messages), "tools": list(tools), "model": model})
        if len(self.calls) == 1:
            await handler.handle_incoming(make_msg("и ещё вопрос: есть VIP?"))
            return LlmResponse(text="ответ на первое")
        return LlmResponse(text="ответ на оба")


async def test_message_during_turn_gets_answered(env, pool):
    env.llm = _SecondMessageDuringTurn()
    await handler.handle_incoming(make_msg("Привет, сколько стоит час?"))
    await env.idle()
    await env.idle()
    assert len(env.llm.calls) == 2
    joined = "\n".join(_user_texts(env.llm.calls[1]))
    assert "сколько стоит час" in joined and "есть VIP" in joined
    assert env.gw.texts(CLIENT_JID) == ["ответ на оба"]
    rows = await pool.fetch("SELECT author FROM messages ORDER BY id")
    assert [r["author"] for r in rows] == ["client", "client", "bot"]


async def test_turn_exception_retried_with_backoff(env, pool, monkeypatch):
    real = turn.run_turn
    calls = []

    async def flaky(cid, **kw):
        calls.append(cid)
        if len(calls) == 1:
            raise RuntimeError("db hiccup")
        return await real(cid, **kw)

    monkeypatch.setattr(turn, "run_turn", flaky)
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    assert len(calls) == 2
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте! Чем помочь?"]


async def test_turn_exception_gives_up_after_three_attempts(env, pool, monkeypatch):
    calls = []

    async def broken(cid, **kw):
        calls.append(cid)
        raise RuntimeError("always")

    monkeypatch.setattr(turn, "run_turn", broken)
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    assert len(calls) == 3
    assert env.gw.sent == []


async def _unanswered(pool, jid=CLIENT_JID, phone="77010000001", age=timedelta(minutes=2)):
    cid = await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone, last_message_at) VALUES ($1, $2, now()) RETURNING id", jid, phone,
    )
    await pool.execute(
        "INSERT INTO messages(conversation_id, direction, author, text, created_at)"
        " VALUES ($1, 'in', 'client', 'Есть места?', $2)", cid, datetime.now(UTC) - age,
    )
    return cid


async def test_periodic_sweeper_recovers_lost_reply_once(env, pool):
    cid = await _unanswered(pool)
    assert await handler.sweep_unanswered() == 1
    await env.idle()
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте! Чем помочь?"]
    # answered now: further sweeps do nothing, and a re-run of the same turn never double-sends
    assert await handler.sweep_unanswered() == 0
    await pool.execute("DELETE FROM messages WHERE author = 'bot'")
    await turn.run_turn(cid)
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте! Чем помочь?"]


async def test_sweeper_skips_pending_buffer_and_fresh_messages(env, pool, monkeypatch):
    from iler.engine import buffer

    await _unanswered(pool)
    await _unanswered(pool, jid="77010000002@s.whatsapp.net", phone="77010000002", age=timedelta(seconds=1))
    monkeypatch.setattr(buffer, "pending", lambda key: key == CLIENT_JID)
    assert await handler.sweep_unanswered() == 0
    await env.idle()
    assert env.gw.sent == []


async def test_sweeper_gives_up_on_a_message_after_a_few_sweeps(env, pool, monkeypatch):
    calls = []

    async def broken(cid, **kw):
        calls.append(cid)
        raise RuntimeError("always")

    monkeypatch.setattr(turn, "run_turn", broken)
    await _unanswered(pool)
    scheduled = []
    for _ in range(handler.MAX_SWEEPS_PER_MESSAGE + 2):
        scheduled.append(await handler.sweep_unanswered())
        await env.idle()
    assert sum(scheduled) == handler.MAX_SWEEPS_PER_MESSAGE


def _bad_hours(day) -> WeeklyHours:
    return WeeklyHours.model_construct(always_open=False, mon=day, tue=day, wed=day, thu=day, fri=day,
                                       sat=day, sun=day)


def test_invalid_hours_treated_as_open():
    hours = _bad_hours(DayHours.model_construct(open="", close="", closed=False))
    assert admission.within_hours(hours, datetime(2026, 11, 11, 12, 0))
    assert admission.within_hours(hours, datetime(2026, 11, 11, 3, 0))
    hours = _bad_hours(DayHours.model_construct(open="ab:cd", close="25", closed=False))
    assert admission.within_hours(hours, datetime(2026, 11, 11, 12, 0))


async def test_invalid_hours_do_not_crash_turn(env, monkeypatch):
    from iler import settings_store

    real = settings_store.load_all

    async def patched():
        s = await real()
        s.bot.hours = _bad_hours(DayHours.model_construct(open="", close="", closed=False))
        return s

    monkeypatch.setattr(settings_store, "load_all", patched)
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте! Чем помочь?"]


async def test_after_hours_fixed_message_sent_once_per_12h(env, pool):
    await env.set_bot(hours=_closed_hours(), after_hours_mode="fixed_message",
                      after_hours_message="Мы закрыты, ответим утром")
    await handler.handle_incoming(make_msg("Вы работаете?"))
    await env.idle()
    await handler.handle_incoming(make_msg("Алло?"))
    await env.idle()
    assert env.gw.texts(CLIENT_JID) == ["Мы закрыты, ответим утром"]
    # 13 hours later the text goes out again
    await pool.execute("UPDATE messages SET created_at = now() - interval '13 hours' WHERE author = 'bot'")
    await handler.handle_incoming(make_msg("Доброе утро?"))
    await env.idle()
    assert env.gw.texts(CLIENT_JID) == ["Мы закрыты, ответим утром"] * 2


async def test_handover_with_empty_text_sends_polite_phrase(env, pool):
    from iler.llm.base import ToolCall

    await env.set_notify(admin_phones=[ADMIN_PHONE])
    env.llm = FakeLLM([
        LlmResponse(text="", tool_calls=[ToolCall(id="h1", name="handover_to_operator", arguments={"reason": "жалоба"})]),
        LlmResponse(text=""),
    ])
    await handler.handle_incoming(make_msg("Позовите человека"))
    await env.idle()
    assert turn.HANDOVER_REPLY == "Передаю ваш вопрос администратору, он скоро ответит."
    assert env.gw.texts(CLIENT_JID) == [turn.HANDOVER_REPLY]
    assert (await _conv(pool))["status"] == "needs_operator"


async def test_bot_not_configured_does_not_reply(env, pool, monkeypatch):
    from types import SimpleNamespace

    from iler import settings_store

    real = settings_store.load_all

    async def patched():
        s = await real()
        fields = {k: getattr(s.bot, k) for k in type(s.bot).model_fields}
        fields["prompt"] = "   "  # works before and after BotSettings gains a `prompt` field
        bot = SimpleNamespace(**fields)
        return SimpleNamespace(bot=bot, llm=s.llm, notify=s.notify, senet=s.senet)

    monkeypatch.setattr(settings_store, "load_all", patched)
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    assert env.llm.calls == []
    assert env.gw.sent == []


def test_is_admin_normalizes_local_8_prefix():
    notify = NotifySettings(admin_phones=["8 701 000 00 99"])
    assert admission.is_admin(ADMIN_JID, notify)
    assert admission.is_admin("77010000099", notify)
    notify = NotifySettings(admin_phones=["+7 701 000 00 99"])
    assert admission.is_admin("87010000099", notify)
    assert not admission.is_admin("87010000098", notify)
    assert admission.normalize_phone("87011234567") == "77011234567"
    assert admission.normalize_phone("8701") == "8701"


def test_lid_chat_without_phone_logged_once(caplog):
    import logging

    notify = NotifySettings(admin_phones=[ADMIN_PHONE])
    msg = make_msg("+1", phone="", jid="123456789012345@lid")
    with caplog.at_level(logging.INFO, logger="iler.engine.admission"):
        assert not admission.is_admin_chat(msg, notify)
        assert not admission.is_admin_chat(msg, notify)
    assert sum("123456789012345@lid" in r.getMessage() for r in caplog.records) == 1


async def test_coin_limit_hands_over_without_calling_llm(env, pool, monkeypatch):
    from iler import usage

    async def reached():
        return True

    monkeypatch.setattr(usage, "limit_reached", reached)
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    env.llm = FakeLLM(script=[])  # must not be called
    await handler.handle_incoming(make_msg("Привет"))
    await env.idle()
    conv = await _conv(pool)
    assert conv["status"] == "needs_operator"
    assert env.gw.texts(CLIENT_JID) == ["Передаю ваш вопрос администратору, он скоро ответит."]
    assert len(env.gw.texts(ADMIN_JID)) == 1 and "Лимит коинов" in env.gw.texts(ADMIN_JID)[0]
