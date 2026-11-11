"""Evolution webhook, inbound dispatch and the inbound retry worker (Task 4)."""

import pytest

from iler import events, inbound, settings_store
from iler.settings_models import NotifySettings
from iler.workers import inbound_retry

HEADERS = {"apikey": "test-evo-key"}
URL = "/internal/evolution/webhook"


def upsert(mid="3EB0AAA", jid="77010000001@s.whatsapp.net", text="Привет", from_me=False, **key_extra):
    return {
        "event": "messages.upsert",
        "instance": "iler",
        "data": {
            "key": {"remoteJid": jid, "fromMe": from_me, "id": mid, **key_extra},
            "pushName": "Айдар",
            "message": {"conversation": text},
            "messageType": "conversation",
            "messageTimestamp": 1_700_000_000,
        },
    }


@pytest.fixture
def captured():
    got = []

    async def handler(msg):
        got.append(msg)

    inbound.set_handler(handler)
    yield got
    inbound.set_handler(None)


async def test_webhook_rejects_bad_apikey(client, pool):
    r = await client.post(URL, json=upsert(), headers={"apikey": "wrong"})
    assert r.status_code == 401
    r = await client.post(URL, json=upsert())
    assert r.status_code == 401
    assert await pool.fetchval("SELECT count(*) FROM inbound_events") == 0


async def test_webhook_dedup_by_evo_id(client, pool, captured):
    r1 = await client.post(URL, json=upsert(), headers=HEADERS)
    r2 = await client.post(URL, json=upsert(), headers=HEADERS)
    assert r1.status_code == 200 and r2.status_code == 200
    await inbound.wait_idle()
    assert await pool.fetchval("SELECT count(*) FROM inbound_events") == 1
    assert len(captured) == 1 and captured[0].text == "Привет"
    assert await pool.fetchval("SELECT status FROM inbound_events") == "processed"


async def test_webhook_not_rate_limited(client, pool):
    for i in range(200):
        r = await client.post(URL, json=upsert(mid=f"ID{i}"), headers=HEADERS)
        assert r.status_code == 200
    await inbound.wait_idle()
    assert await pool.fetchval("SELECT count(*) FROM inbound_events") == 200


async def test_webhook_without_handler_keeps_event_received(client, pool):
    inbound.set_handler(None)
    r = await client.post(URL, json=upsert(), headers=HEADERS)
    assert r.status_code == 200
    await inbound.wait_idle()
    assert await pool.fetchval("SELECT status FROM inbound_events") == "received"


async def test_webhook_ignores_other_instance_and_garbage(client, pool):
    other = upsert()
    other["instance"] = "someone-else"
    assert (await client.post(URL, json=other, headers=HEADERS)).status_code == 200
    r = await client.post(URL, content=b"not json", headers={**HEADERS, "content-type": "application/json"})
    assert r.status_code == 200
    assert (await client.post(URL, json={"event": "contacts.update", "data": []}, headers=HEADERS)).status_code == 200
    assert await pool.fetchval("SELECT count(*) FROM inbound_events") == 0


async def test_connection_update_instance_is_string(client, pool):
    payload = {
        "event": "connection.update",
        "instance": "iler",
        "data": {"instance": "iler", "state": "open", "statusReason": 200, "wuid": "77010000009@s.whatsapp.net"},
    }
    async with events.subscription() as q:
        r = await client.post(URL, json=payload, headers=HEADERS)
        evs = [q.get_nowait() for _ in range(q.qsize())]
    assert r.status_code == 200
    row = await pool.fetchrow("SELECT status, phone, last_seen_at FROM wa_instance WHERE id=1")
    assert row["status"] == "open" and row["phone"] == "77010000009" and row["last_seen_at"] is not None
    assert any(e["type"] == "whatsapp.status" and e["data"]["status"] == "open" for e in evs)

    payload["data"] = {"instance": "iler", "state": "close", "statusReason": 401}
    await client.post(URL, json=payload, headers=HEADERS)
    row = await pool.fetchrow("SELECT status, last_error FROM wa_instance WHERE id=1")
    assert row["status"] == "close" and row["last_error"] == "logged_out"


async def test_group_and_status_messages_ignored(client, pool, captured):
    await client.post(URL, json=upsert(mid="G1", jid="120363000000@g.us", participant="77010000003@s.whatsapp.net"),
                      headers=HEADERS)
    await client.post(URL, json=upsert(mid="S1", jid="status@broadcast"), headers=HEADERS)
    await inbound.wait_idle()
    assert captured == []
    statuses = {r["evo_message_id"]: r["status"] for r in await pool.fetch("SELECT * FROM inbound_events")}
    assert statuses == {"G1": "ignored", "S1": "ignored"}


async def test_admin_group_messages_pass(client, pool, captured):
    await settings_store.put("notify", NotifySettings(admin_group_jid="120363000000@g.us"))
    await client.post(URL, json=upsert(mid="G2", text="+12", jid="120363000000@g.us",
                                       participant="77010000003@s.whatsapp.net"), headers=HEADERS)
    await inbound.wait_idle()
    assert len(captured) == 1 and captured[0].is_group and captured[0].text == "+12"


async def test_from_me_passed_to_handler(client, captured):
    await client.post(URL, json=upsert(mid="ME1", from_me=True, text="я оператор"), headers=HEADERS)
    await inbound.wait_idle()
    assert len(captured) == 1 and captured[0].from_me


async def test_handler_error_returns_event_to_received_then_failed(client, pool):
    async def bad(msg):
        raise RuntimeError("engine down")

    inbound.set_handler(bad)
    try:
        await client.post(URL, json=upsert(), headers=HEADERS)
        await inbound.wait_idle()
        row = await pool.fetchrow("SELECT status, attempts, error FROM inbound_events")
        assert row["status"] == "received" and row["attempts"] == 1 and "engine down" in row["error"]
        await pool.execute(f"UPDATE inbound_events SET attempts = {inbound.MAX_ATTEMPTS - 1},"
                           " updated_at = now() - interval '1 minute'")
        await inbound_retry.retry_once()
        assert await pool.fetchval("SELECT status FROM inbound_events") == "failed"
    finally:
        inbound.set_handler(None)


async def test_inbound_retry_after_restart(pool, captured):
    """Server restarted mid-buffer: a stored `received` event is re-dispatched by the retry worker."""
    payload = upsert(mid="LOST1", text="вы работаете?")
    await pool.execute(
        "INSERT INTO inbound_events(evo_message_id, payload, status, created_at, updated_at)"
        " VALUES ('LOST1', $1, 'received', now() - interval '45 seconds', now() - interval '45 seconds')",
        payload,
    )
    # a fresh event (< 30 s) belongs to the webhook task, the worker leaves it alone
    await pool.execute(
        "INSERT INTO inbound_events(evo_message_id, payload) VALUES ('FRESH1', $1)", upsert(mid="FRESH1")
    )
    # an event stuck in `processing` from the previous process
    await pool.execute(
        "INSERT INTO inbound_events(evo_message_id, payload, status, created_at, updated_at)"
        " VALUES ('STUCK1', $1, 'processing', now() - interval '20 seconds', now() - interval '20 seconds')",
        upsert(mid="STUCK1", text="второе"),
    )
    await inbound_retry.recover_after_restart()
    await pool.execute("UPDATE inbound_events SET updated_at = now() - interval '1 minute' WHERE evo_message_id='STUCK1'")
    n = await inbound_retry.retry_once()
    assert n == 2
    assert [m.evo_id for m in captured] == ["LOST1", "STUCK1"]
    assert captured[0].text == "вы работаете?"
    statuses = {r["evo_message_id"]: r["status"] for r in await pool.fetch("SELECT * FROM inbound_events")}
    assert statuses == {"LOST1": "processed", "STUCK1": "processed", "FRESH1": "received"}


async def test_dispatch_is_claimed_once(pool, captured):
    row = await pool.fetchrow(
        "INSERT INTO inbound_events(evo_message_id, payload) VALUES ('C1', $1) RETURNING id, payload, created_at",
        upsert(mid="C1"),
    )
    import asyncio

    await asyncio.gather(inbound.dispatch(row), inbound.dispatch(row), inbound.dispatch(dict(row)))
    assert len(captured) == 1
