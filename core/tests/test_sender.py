"""Outbound path: split, presence, idempotent intents, operator messages, reconcile worker (Task 5)."""

from datetime import UTC, datetime, timedelta

import pytest
from engine_fakes import ADMIN_GROUP, ADMIN_JID, ADMIN_PHONE, CLIENT_JID, env  # noqa: F401

from iler import events
from iler.engine import sender
from iler.whatsapp.evolution import EvolutionError, SendUncertain
from iler.workers import outbound_reconcile


async def _conv(pool) -> int:
    return await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone, name) VALUES ($1, '77010000001', 'Айдар') RETURNING id",
        CLIENT_JID,
    )


def test_split_reply():
    assert sender.split_reply("a[SPLIT]b [SPLIT] c") == ["a", "b", "c"]
    assert sender.split_reply("Первый абзац\n\nВторой\nстрока\n\n\nТретий") == [
        "Первый абзац", "Второй\nстрока", "Третий",
    ]
    assert sender.split_reply("1\n\n2\n\n3\n\n4\n\n5\n\n6") == ["1", "2", "3", "4\n\n5\n\n6"]
    assert sender.split_reply("  \n\n ") == []


def test_presence_ms():
    assert sender.presence_ms("abc") == 120
    assert sender.presence_ms("x" * 500) == 4000


async def test_outbound_idempotent_retry(env, pool):
    cid = await _conv(pool)
    assert await sender.send_reply(cid, ["Привет", "Как дела?"], turn_id="t1") == ["sent", "sent"]
    assert await sender.send_reply(cid, ["Привет", "Как дела?"], turn_id="t1") == ["sent", "sent"]
    assert env.gw.texts(CLIENT_JID) == ["Привет", "Как дела?"]
    rows = await pool.fetch("SELECT idem_key, status, evo_message_id FROM outbound_intents ORDER BY id")
    assert [r["idem_key"] for r in rows] == [f"{cid}:t1:0", f"{cid}:t1:1"]
    assert all(r["status"] == "sent" and r["evo_message_id"] for r in rows)
    assert await pool.fetchval("SELECT count(*) FROM messages WHERE author = 'bot'") == 2


async def test_send_timeout_is_uncertain_and_not_resent(env, pool):
    cid = await _conv(pool)
    env.gw.fail = SendUncertain("read timeout")
    assert await sender.send_reply(cid, ["Привет", "второе"], turn_id="t2") == ["uncertain"]
    env.gw.fail = None
    assert await sender.send_reply(cid, ["Привет", "второе"], turn_id="t2") == ["uncertain", "sent"]
    assert env.gw.texts(CLIENT_JID) == ["второе"]
    assert await outbound_reconcile.reconcile_once() == 0
    assert env.gw.texts(CLIENT_JID) == ["второе"]


async def test_send_error_is_failed_then_retried_once(env, pool):
    cid = await _conv(pool)
    env.gw.fail = EvolutionError("evolution unreachable")
    assert await sender.send_reply(cid, ["Привет"], turn_id="t3") == ["failed"]
    assert await pool.fetchval("SELECT status FROM outbound_intents") == "failed"
    assert await pool.fetchval("SELECT count(*) FROM messages") == 0
    env.gw.fail = None
    await pool.execute("UPDATE outbound_intents SET updated_at = now() - interval '1 minute'")
    assert await outbound_reconcile.reconcile_once() == 1
    assert await outbound_reconcile.reconcile_once() == 0
    assert env.gw.texts(CLIENT_JID) == ["Привет"]
    assert await pool.fetchval("SELECT status FROM outbound_intents") == "sent"


async def test_reconcile_marks_stuck_sending_uncertain(env, pool):
    cid = await _conv(pool)
    await pool.execute(
        "INSERT INTO outbound_intents(idem_key, conversation_id, chat_jid, text, status, updated_at)"
        " VALUES ('x', $1, $2, 'hi', 'sending', now() - interval '10 minutes')", cid, CLIENT_JID,
    )
    await outbound_reconcile.reconcile_once()
    assert await pool.fetchval("SELECT status FROM outbound_intents") == "uncertain"
    assert env.gw.sent == []


async def test_send_operator_message_pauses_and_publishes(env, pool):
    await env.set_bot(handover_pause_minutes=45)
    cid = await _conv(pool)
    async with events.subscription() as q:
        await sender.send_operator_message(cid, "Здравствуйте, это администратор", "Оля", request_id="req-0001")
        got = [q.get_nowait() for _ in range(q.qsize())]
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте, это администратор"]
    conv = await pool.fetchrow("SELECT * FROM conversations WHERE id = $1", cid)
    assert conv["status"] == "paused"
    assert timedelta(minutes=44) < conv["paused_until"] - datetime.now(UTC) <= timedelta(minutes=45, seconds=5)
    assert await pool.fetchval("SELECT author FROM messages") == "operator"
    types = [e["type"] for e in got]
    assert "message.new" in types and "conversation.updated" in types
    msg = next(e for e in got if e["type"] == "message.new")["data"]
    assert msg["conversation_id"] == cid and msg["message"]["author"] == "operator"


async def test_send_operator_message_failure_raises(env, pool):
    cid = await _conv(pool)
    env.gw.fail = EvolutionError("down")
    with pytest.raises(sender.SendFailed):
        await sender.send_operator_message(cid, "Алло", "Оля", request_id="req-0002")


async def test_notify_admins_normalizes_local_8_prefix(env, pool):
    await env.set_notify(admin_phones=["8 701 000 00 99", "+7 701 000 00 99"])
    await sender.notify_admins("Тест", key="test")
    assert [j for j, _ in env.gw.sent] == [ADMIN_JID]


async def test_partial_reply_remaining_parts_created_pending(env, pool):
    cid = await _conv(pool)
    env.gw.fail = EvolutionError("down")
    assert await sender.send_reply(cid, ["a", "b", "c"], turn_id="t5") == ["failed"]
    rows = await pool.fetch("SELECT idem_key, status FROM outbound_intents ORDER BY id")
    assert [(r["idem_key"], r["status"]) for r in rows] == [
        (f"{cid}:t5:0", "failed"), (f"{cid}:t5:1", "pending"), (f"{cid}:t5:2", "pending"),
    ]
    env.gw.fail = None
    await pool.execute("UPDATE outbound_intents SET updated_at = now() - interval '1 minute'")
    assert await outbound_reconcile.reconcile_once() == 3
    assert env.gw.texts(CLIENT_JID) == ["a", "b", "c"]
    assert await pool.fetchval("SELECT count(*) FROM outbound_intents WHERE status = 'sent'") == 3


async def test_reconcile_keeps_part_order(env, pool):
    """A later part is never sent while an earlier one is still undelivered."""
    cid = await _conv(pool)
    env.gw.fail = EvolutionError("down")
    await sender.send_reply(cid, ["a", "b"], turn_id="t6")
    await pool.execute("UPDATE outbound_intents SET updated_at = now() - interval '1 minute'"
                       " WHERE idem_key LIKE '%:1'")  # part 0 too fresh to retry yet
    env.gw.fail = None
    assert await outbound_reconcile.reconcile_once() == 0
    assert env.gw.sent == []


async def _client_msg(pool, cid, text="ещё вопрос"):
    return await pool.fetchval(
        "INSERT INTO messages(conversation_id, direction, author, text) VALUES ($1, 'in', 'client', $2) RETURNING id",
        cid, text,
    )


async def _failed_reply(env, pool, cid, turn_id):
    env.gw.fail = EvolutionError("down")
    await sender.send_reply(cid, ["a", "b"], turn_id=turn_id)
    env.gw.fail = None
    await pool.execute("UPDATE outbound_intents SET updated_at = now() - interval '1 minute'")


async def _assert_all_stale(env, pool):
    assert await outbound_reconcile.reconcile_once() == 0
    assert env.gw.sent == []
    rows = await pool.fetch("SELECT status, error FROM outbound_intents")
    assert rows and all(r["status"] == "failed" and r["error"] == "stale" for r in rows)
    assert await outbound_reconcile.reconcile_once() == 0  # stale intents are not picked up again


async def test_retry_dropped_when_newer_client_message(env, pool):
    cid = await _conv(pool)
    mid = await _client_msg(pool, cid, "первый")
    await _failed_reply(env, pool, cid, str(mid))
    await _client_msg(pool, cid)
    await _assert_all_stale(env, pool)


async def test_retry_dropped_when_paused(env, pool):
    cid = await _conv(pool)
    mid = await _client_msg(pool, cid, "первый")
    await _failed_reply(env, pool, cid, str(mid))
    await pool.execute("UPDATE conversations SET status = 'needs_operator', paused_until = NULL")
    await _assert_all_stale(env, pool)


async def test_retry_dropped_when_bot_disabled(env, pool):
    cid = await _conv(pool)
    mid = await _client_msg(pool, cid, "первый")
    await _failed_reply(env, pool, cid, str(mid))
    await env.set_bot(enabled=False)
    await _assert_all_stale(env, pool)


async def test_notify_admins_phone_and_group(env, pool):
    await env.set_notify(admin_phones=["+7 701 000 00 99"], admin_group_jid=ADMIN_GROUP)
    await sender.notify_admins("Тест уведомления", key="test")
    assert sorted(j for j, _ in env.gw.sent) == sorted([ADMIN_JID, ADMIN_GROUP])
    assert ADMIN_PHONE in ADMIN_JID


async def test_operator_message_repeat_with_same_request_id_sends_once(env, pool):
    cid = await _conv(pool)
    await sender.send_operator_message(cid, "Здравствуйте", "Оля", request_id="req-same-01")
    await sender.send_operator_message(cid, "Здравствуйте", "Оля", request_id="req-same-01")
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте"]
    keys = [r["idem_key"] for r in await pool.fetch("SELECT idem_key FROM outbound_intents")]
    assert keys == [f"op:{cid}:req-same-01"]
    await sender.send_operator_message(cid, "Здравствуйте", "Оля", request_id="req-other-1")  # a new message
    assert env.gw.texts(CLIENT_JID) == ["Здравствуйте", "Здравствуйте"]


async def test_notify_admins_requires_key(env, pool):
    with pytest.raises(TypeError):
        await sender.notify_admins("Тест")  # type: ignore[call-arg]
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    await sender.notify_admins("Тест", key="same")
    await sender.notify_admins("Тест", key="same")
    assert len(env.gw.texts(ADMIN_JID)) == 1
