"""Conversations API, SSE, dashboard, test chat (Task 8)."""

import asyncio
import json
import sys
import types
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient

from iler import events
from iler.api import dashboard as dashboard_api
from iler.api import events as events_api

OWNER = {"email": "owner@club.kz", "password": "owner-pass-123", "name": "Owner"}


def csrf(c: AsyncClient) -> dict:
    return {"X-ILER-CSRF": c.cookies.get("iler_csrf", "")}


async def login(c: AsyncClient) -> None:
    r = await c.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    c.cookies.clear()
    r = await c.post("/api/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    assert r.status_code == 200, r.text


async def make_conv(pool, phone: str, name: str = "", *, status: str = "active", last_at=None, unread: int = 0) -> int:
    return await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone, name, status, last_message_at, unread)"
        " VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        f"{phone}@s.whatsapp.net", phone, name, status, last_at, unread,
    )


async def add_msg(pool, conv_id: int, text: str, author: str = "client", created_at=None) -> int:
    direction = "in" if author == "client" else "out"
    return await pool.fetchval(
        "INSERT INTO messages(conversation_id, direction, author, text, created_at)"
        " VALUES ($1, $2, $3, $4, coalesce($5, now())) RETURNING id",
        conv_id, direction, author, text, created_at,
    )


def fake_engine_module(monkeypatch, name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(f"iler.engine.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"iler.engine.{name}", mod)
    return mod


# --- auth ---------------------------------------------------------------------------------------

ROUTES = [
    ("GET", "/api/conversations"),
    ("GET", "/api/conversations/1/messages"),
    ("POST", "/api/conversations/1/send"),
    ("POST", "/api/conversations/1/takeover"),
    ("POST", "/api/conversations/1/release"),
    ("GET", "/api/events"),
    ("GET", "/api/dashboard"),
    ("POST", "/api/test-chat"),
]


@pytest.mark.parametrize("method,path", ROUTES)
async def test_requires_auth(client, method, path):
    r = await client.request(method, path, json={"text": "x", "request_id": "req-0001"} if method == "POST" else None)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("path", ["/api/conversations/1/send", "/api/conversations/1/takeover",
                                  "/api/conversations/1/release", "/api/test-chat"])
async def test_mutations_require_csrf(client, pool, path):
    await login(client)
    await make_conv(pool, "77010000001")
    r = await client.post(path, json={"text": "hi", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


# --- operator actions ---------------------------------------------------------------------------

async def test_operator_send_marks_author_operator_and_pauses(client, pool, monkeypatch):
    await login(client)
    conv = await make_conv(pool, "77010000001", "Client")
    calls = []

    async def send_operator_message(conversation_id: int, text: str, user_name: str, *, request_id: str) -> None:
        # Mimics the engine contract: store the operator message and pause for handover minutes.
        calls.append((conversation_id, text, user_name, request_id))
        await add_msg(pool, conversation_id, text, author="operator")
        await pool.execute(
            "UPDATE conversations SET status='paused', paused_until = now() + interval '60 minutes' WHERE id=$1",
            conversation_id,
        )

    fake_engine_module(monkeypatch, "sender", send_operator_message=send_operator_message)
    r = await client.post(f"/api/conversations/{conv}/send", json={"text": "Здравствуйте!", "request_id": "req-0001"},
                          headers=csrf(client))
    assert r.status_code == 200, r.text
    assert calls == [(conv, "Здравствуйте!", "Owner", "req-0001")]

    msgs = (await client.get(f"/api/conversations/{conv}/messages")).json()["items"]
    assert msgs[0]["author"] == "operator" and msgs[0]["direction"] == "out"
    item = (await client.get("/api/conversations")).json()["items"][0]
    assert item["status"] == "paused" and item["paused_until"] is not None
    assert item["last_message"]["author"] == "operator"


async def test_send_validation_and_not_found(client, pool, monkeypatch):
    await login(client)
    conv = await make_conv(pool, "77010000001")

    async def boom(*a, **k):
        raise AssertionError("must not be called")

    fake_engine_module(monkeypatch, "sender", send_operator_message=boom)
    for bad in ["", "   ", "x" * 4001]:
        r = await client.post(f"/api/conversations/{conv}/send", json={"text": bad, "request_id": "req-0001"},
                              headers=csrf(client))
        assert r.status_code == 422, bad
    # request_id is the idempotency key of the send: required and limited to a safe alphabet
    for body in [{"text": "hi"}, {"text": "hi", "request_id": "short"}, {"text": "hi", "request_id": "bad id!x"},
                 {"text": "hi", "request_id": "x" * 65}]:
        r = await client.post(f"/api/conversations/{conv}/send", json=body, headers=csrf(client))
        assert r.status_code == 422, body
    r = await client.post("/api/conversations/999/send", json={"text": "hi", "request_id": "req-0001"},
                          headers=csrf(client))
    assert r.status_code == 404


async def test_takeover_release(client, pool):
    await login(client)
    conv = await make_conv(pool, "77010000001")
    await pool.execute("UPDATE conversations SET paused_until = now() + interval '1 hour' WHERE id=$1", conv)

    async with events.subscription() as q:
        r = await client.post(f"/api/conversations/{conv}/takeover", headers=csrf(client))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "paused" and r.json()["paused_until"] is None
        ev = q.get_nowait()  # published by engine/store, not by the API
        assert ev["type"] == "conversation.updated" and ev["data"]["conversation"]["id"] == conv
        assert ev["data"]["conversation"]["status"] == "paused"
        assert q.empty()

        r = await client.post(f"/api/conversations/{conv}/release", headers=csrf(client))
        assert r.status_code == 200
        assert r.json()["status"] == "active" and r.json()["paused_until"] is None
        ev = q.get_nowait()
        assert ev["type"] == "conversation.updated" and ev["data"]["conversation"]["status"] == "active"
        assert q.empty()

    row = await pool.fetchrow("SELECT status, paused_until FROM conversations WHERE id=$1", conv)
    assert row["status"] == "active" and row["paused_until"] is None
    actions = [r["action"] for r in await pool.fetch("SELECT action FROM audit_log ORDER BY id")]
    assert "conversation.takeover" in actions and "conversation.release" in actions
    assert (await client.post("/api/conversations/999/takeover", headers=csrf(client))).status_code == 404


# --- listing ------------------------------------------------------------------------------------

async def test_list_filters_and_search_by_phone_or_name(client, pool):
    await login(client)
    now = datetime.now(UTC)
    a = await make_conv(pool, "77011112233", "Алия", last_at=now - timedelta(minutes=1))
    b = await make_conv(pool, "77029998877", "Bob_Smith", status="needs_operator", last_at=now)
    c = await make_conv(pool, "77035550000", "", status="paused", last_at=now - timedelta(minutes=5))

    items = (await client.get("/api/conversations")).json()["items"]
    assert [i["id"] for i in items] == [b, a, c]

    r = await client.get("/api/conversations", params={"status": "needs_operator"})
    assert [i["id"] for i in r.json()["items"]] == [b]
    r = await client.get("/api/conversations", params={"q": "1112"})
    assert [i["id"] for i in r.json()["items"]] == [a]
    r = await client.get("/api/conversations", params={"q": "+7 703 555"})
    assert [i["id"] for i in r.json()["items"]] == [c]
    r = await client.get("/api/conversations", params={"q": "алия"})  # case-insensitive name
    assert [i["id"] for i in r.json()["items"]] == [a]
    r = await client.get("/api/conversations", params={"q": "bob_s"})
    assert [i["id"] for i in r.json()["items"]] == [b]
    r = await client.get("/api/conversations", params={"q": "%"})  # wildcard is literal
    assert r.json()["items"] == []
    r = await client.get("/api/conversations", params={"status": "paused", "q": "7703"})
    assert [i["id"] for i in r.json()["items"]] == [c]
    assert (await client.get("/api/conversations", params={"status": "bogus"})).status_code == 422
    assert (await client.get("/api/conversations", params={"limit": 101})).status_code == 422


async def test_pagination_stable(client, pool):
    await login(client)
    base = datetime(2026, 11, 1, tzinfo=UTC)
    ids = []
    for i in range(7):
        # Two conversations share each timestamp to exercise the id tie-break; one has no messages yet.
        last = None if i == 6 else base + timedelta(minutes=i // 2)
        ids.append(await make_conv(pool, f"7701000000{i}", last_at=last))
    expected = (await client.get("/api/conversations", params={"limit": 100})).json()["items"]
    expected_ids = [i["id"] for i in expected]
    assert expected_ids[-1] == ids[6]  # no messages -> last

    seen, cursor = [], None
    first = True
    while True:
        params = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get("/api/conversations", params=params)).json()
        seen += [i["id"] for i in page["items"]]
        if first:
            # A brand-new conversation arriving mid-pagination must not shift later pages.
            await make_conv(pool, "77019999999", last_at=datetime.now(UTC))
            first = False
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == expected_ids
    assert (await client.get("/api/conversations", params={"cursor": "garbage"})).status_code == 422


async def test_messages_pagination_newest_first_and_resets_unread(client, pool):
    await login(client)
    conv = await make_conv(pool, "77010000001", unread=3)
    mids = [await add_msg(pool, conv, f"m{i}") for i in range(5)]
    async with events.subscription() as q:
        page1 = (await client.get(f"/api/conversations/{conv}/messages", params={"limit": 2})).json()
        assert q.get_nowait()["data"]["conversation"]["unread"] == 0  # published by engine/store
    assert [m["id"] for m in page1["items"]] == [mids[4], mids[3]]
    page2 = (await client.get(f"/api/conversations/{conv}/messages",
                              params={"limit": 2, "cursor": page1["next_cursor"]})).json()
    assert [m["id"] for m in page2["items"]] == [mids[2], mids[1]]
    page3 = (await client.get(f"/api/conversations/{conv}/messages",
                              params={"limit": 2, "cursor": page2["next_cursor"]})).json()
    assert [m["id"] for m in page3["items"]] == [mids[0]] and page3["next_cursor"] is None
    assert await pool.fetchval("SELECT unread FROM conversations WHERE id=$1", conv) == 0
    assert (await client.get("/api/conversations/999/messages")).status_code == 404


# --- SSE ----------------------------------------------------------------------------------------

def _cookie_header(c: AsyncClient) -> bytes:
    return "; ".join(f"{k}={v}" for k, v in c.cookies.items()).encode()


async def _open_sse(app, cookie: bytes):
    """Drive the ASGI app directly so the streaming body can be read incrementally."""
    disconnect = asyncio.Event()
    sent: asyncio.Queue = asyncio.Queue()
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        await sent.put(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/api/events", "raw_path": b"/api/events",
        "query_string": b"", "root_path": "", "headers": [(b"host", b"test"), (b"cookie", cookie)],
        "client": ("127.0.0.1", 1234), "server": ("test", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    return task, sent, disconnect


async def _read_until(sent: asyncio.Queue, needle: str, timeout: float = 5.0) -> str:
    buf = ""
    async with asyncio.timeout(timeout):
        while needle not in buf:
            msg = await sent.get()
            if msg["type"] == "http.response.body":
                buf += msg.get("body", b"").decode()
    return buf


async def test_sse_receives_message_new(app, client, monkeypatch):
    await login(client)
    monkeypatch.setattr(events_api, "HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(events_api, "DISCONNECT_POLL_SECONDS", 0.05)
    task, sent, disconnect = await _open_sse(app, _cookie_header(client))
    start = await sent.get()
    assert start["type"] == "http.response.start" and start["status"] == 200
    headers = dict(start["headers"])
    assert headers[b"content-type"].startswith(b"text/event-stream")
    await _read_until(sent, ": connected")

    events.publish("message.new", {"conversation_id": 5, "text": "Привет"})
    buf = await _read_until(sent, "\n\n")
    assert buf.startswith("event: message.new\ndata: ")
    data = json.loads(buf.split("data: ", 1)[1].strip())
    assert data == {"conversation_id": 5, "text": "Привет"}

    assert ": ping" in await _read_until(sent, ": ping")  # heartbeat

    disconnect.set()
    async with asyncio.timeout(5):
        await task
    assert not events._subscribers  # subscription released


async def test_sse_stops_on_client_disconnect(app, client, monkeypatch):
    await login(client)
    monkeypatch.setattr(events_api, "DISCONNECT_POLL_SECONDS", 0.05)
    task, sent, disconnect = await _open_sse(app, _cookie_header(client))
    await _read_until(sent, ": connected")
    assert len(events._subscribers) == 1
    disconnect.set()
    async with asyncio.timeout(5):
        await task
    assert not events._subscribers


# --- test chat ----------------------------------------------------------------------------------

@dataclass
class FakeOutcome:
    reply_parts: list[str]
    tool_calls: list[dict] = field(default_factory=list)
    handed_over: bool = False


async def test_test_chat_does_not_touch_whatsapp_or_db_conversations(client, pool, monkeypatch):
    await login(client)
    seen = {}

    async def run_turn(conversation_id, *, dry_run=False, history=None, phone=""):
        seen.update(conversation_id=conversation_id, dry_run=dry_run, history=history)
        return FakeOutcome(["Здравствуйте!", "Чем помочь?"],
                           [{"name": "get_club_info", "args": {"section": "prices"}, "result": "ok", "error": None}])

    fake_engine_module(monkeypatch, "turn", run_turn=run_turn)

    import iler.whatsapp.evolution as evo

    async def no_send(*a, **k):
        raise AssertionError("WhatsApp must not be used")

    for attr in ("send_text", "send_presence"):
        if hasattr(evo.EvolutionGateway, attr):
            monkeypatch.setattr(evo.EvolutionGateway, attr, no_send)

    body = {"messages": [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
        {"role": "user", "content": "Сколько стоит час?"},
    ]}
    r = await client.post("/api/test-chat", json=body, headers=csrf(client))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "reply": "Здравствуйте!\n\nЧем помочь?",
        "tool_calls": [{"name": "get_club_info", "args": {"section": "prices"}, "result": "ok", "error": None}],
    }
    assert seen["conversation_id"] is None and seen["dry_run"] is True
    assert [(m.role, m.content) for m in seen["history"]] == [(m["role"], m["content"]) for m in body["messages"]]
    for table in ("conversations", "messages", "outbound_intents"):
        assert await pool.fetchval(f"SELECT count(*) FROM {table}") == 0


async def test_test_chat_validation(client, monkeypatch):
    await login(client)

    async def run_turn(*a, **k):
        raise AssertionError("must not be called")

    fake_engine_module(monkeypatch, "turn", run_turn=run_turn)
    bad_bodies = [
        {"messages": []},
        {"messages": [{"role": "assistant", "content": "hi"}]},
        {"messages": [{"role": "system", "content": "hi"}]},
        {"messages": [{"role": "user", "content": "hi"}] * 51},
    ]
    for body in bad_bodies:
        r = await client.post("/api/test-chat", json=body, headers=csrf(client))
        assert r.status_code == 422, body


async def test_test_chat_llm_error_is_503(client, monkeypatch):
    await login(client)
    from iler.llm.base import LlmError

    async def run_turn(*a, **k):
        raise LlmError("all providers failed", retryable=False)

    fake_engine_module(monkeypatch, "turn", run_turn=run_turn)
    r = await client.post("/api/test-chat", json={"messages": [{"role": "user", "content": "hi"}]},
                          headers=csrf(client))
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_error"


async def test_test_chat_hides_provider_auth_error(client, monkeypatch):
    await login(client)
    from iler.llm.base import LlmError

    async def run_turn(*a, **k):
        raise LlmError("all LLM providers failed: anthropic/claude-sonnet-5-5: anthropic HTTP 401: invalid"
                       " x-api-key sk-ant-***wxyz; openai/gpt-5.4: openai connection error: ConnectError",
                       retryable=False)

    fake_engine_module(monkeypatch, "turn", run_turn=run_turn)
    r = await client.post("/api/test-chat", json={"messages": [{"role": "user", "content": "hi"}]},
                          headers=csrf(client))
    assert r.status_code == 424 and "wxyz" not in r.text
    assert r.json()["error"]["message"] == "anthropic: Провайдер модели отклонил ключ доступа. Обратитесь в ILER."


# --- dashboard ----------------------------------------------------------------------------------

async def test_dashboard_counts_today_in_club_tz(client, pool, monkeypatch):
    await login(client)
    # 01:00 in Asia/Almaty (UTC+5) on 2026-11-11 == 2026-11-10 20:00 UTC.
    now = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)
    monkeypatch.setattr(dashboard_api, "_now", lambda: now)
    today_local = datetime(2026, 11, 10, 19, 30, tzinfo=UTC)  # 00:30 local -> today
    yesterday_local = datetime(2026, 11, 10, 18, 30, tzinfo=UTC)  # 23:30 local the day before (same UTC date)

    c1 = await make_conv(pool, "77010000001")
    c2 = await make_conv(pool, "77010000002")
    await add_msg(pool, c1, "hi", "client", today_local)
    await add_msg(pool, c1, "hello", "bot", today_local)
    await add_msg(pool, c1, "q2", "client", today_local + timedelta(minutes=5))
    await add_msg(pool, c2, "old", "client", yesterday_local)
    await add_msg(pool, c2, "old reply", "bot", yesterday_local)
    await pool.execute(
        "INSERT INTO booking_requests(conversation_id, payload, status, created_at) VALUES"
        " ($1, '{}', 'pending', $2), ($1, '{}', 'confirmed', $2), ($1, '{}', 'pending', $3)",
        c1, today_local, yesterday_local,
    )
    await pool.execute(
        "INSERT INTO ai_requests(provider, model, error, created_at) VALUES"
        " ('openai', 'm', 'timeout', $1), ('openai', 'm', NULL, $1), ('openai', 'm', 'old', $2)",
        now - timedelta(hours=1), now - timedelta(hours=30),
    )
    await pool.execute(
        "INSERT INTO outbound_intents(idem_key, chat_jid, text, status, updated_at) VALUES"
        " ('a', 'j', 't', 'failed', $1), ('b', 'j', 't', 'uncertain', $1), ('c', 'j', 't', 'sent', $1),"
        " ('d', 'j', 't', 'failed', $2)",
        now - timedelta(hours=2), now - timedelta(hours=25),
    )
    await pool.execute(
        "INSERT INTO wa_instance(id, evo_name, phone, status) VALUES (1, 'iler', '77001234567', 'open')"
        " ON CONFLICT (id) DO UPDATE SET phone = EXCLUDED.phone, status = EXCLUDED.status"
    )

    r = await client.get("/api/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()
    health, coins = body.pop("health"), body.pop("coins")
    assert health["answered_share_today"] == 1.0 and health["handovers_today"] == 0
    assert health["last_bot_reply_at"].startswith("2026-11-10T19:30") and health["avg_reply_ms_today"] == 0
    assert coins["limit"] == 0 and coins["spent"] == 0 and coins["remaining"] is None
    assert body == {
        "whatsapp": {"status": "open", "phone": "77001234567"},
        "bot_enabled": True,
        "today": {"conversations": 1, "client_messages": 2, "bot_replies": 1,
                  "bookings_pending": 2, "bookings_total": 2},
        "errors_24h": {"llm": 1, "send": 2},
        "backup": {"status": None, "at": None, "message": None},
    }
    from iler.backup import status as backup_status

    await backup_status.record(False, "disk full")
    backup = (await client.get("/api/dashboard")).json()["backup"]
    assert backup["status"] == "error" and backup["message"] == "disk full" and backup["at"]


async def test_dashboard_handovers_any_reason_and_handover_reply_not_answered(client, pool, monkeypatch):
    from iler.engine import store
    from iler.engine.turn import HANDOVER_REPLY

    assert dashboard_api.HANDOVER_REPLY == HANDOVER_REPLY  # the dashboard keeps its own copy
    await login(client)
    now = datetime.now(UTC)
    monkeypatch.setattr(dashboard_api, "_now", lambda: now)
    c1 = await make_conv(pool, "77010000001")
    c2 = await make_conv(pool, "77010000002")
    c3 = await make_conv(pool, "77010000003")
    await add_msg(pool, c1, "hi", "client", now)
    await add_msg(pool, c1, HANDOVER_REPLY, "bot", now)  # coin limit / LLM failure handover
    await add_msg(pool, c2, "hi", "client", now)
    await add_msg(pool, c2, "Здравствуйте!", "bot", now)
    await add_msg(pool, c3, "hi", "client", now)
    await store.set_status(c1, "needs_operator", pause_minutes=60)  # e.g. coin limit
    await store.set_status(c3, "needs_operator", pause_minutes=60)  # e.g. LLM failure
    await store.set_status(c3, "active", clear_pause=True)  # released by the operator later: still counted
    health = (await client.get("/api/dashboard")).json()["health"]
    assert health["handovers_today"] == 2
    assert health["answered_share_today"] == round(1 / 3, 3)


def test_today_bounds_dst_zone():
    start, end = dashboard_api.today_bounds(datetime(2026, 3, 29, 12, 0, tzinfo=UTC), "Europe/Berlin")
    assert start == datetime(2026, 3, 28, 23, 0, tzinfo=UTC)  # CET midnight
    assert end == datetime(2026, 3, 29, 22, 0, tzinfo=UTC)  # CEST midnight (23 h day)


async def test_test_chat_bot_not_configured_409(client, monkeypatch):
    import sys
    import types

    from iler.engine.prompt import BotNotConfigured

    async def fake_run_turn(*a, **k):
        raise BotNotConfigured()

    monkeypatch.setitem(sys.modules, "iler.engine.turn", types.SimpleNamespace(run_turn=fake_run_turn))
    await login(client)
    r = await client.post("/api/test-chat", json={"messages": [{"role": "user", "content": "Привет"}]},
                          headers=csrf(client))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "bot_not_configured"


async def test_dashboard_coins_spend_and_forecast(client, pool, monkeypatch):
    from iler.config import get_settings

    await login(client)
    now = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)  # 2026-11-11 01:00 in Asia/Almaty
    monkeypatch.setattr(dashboard_api, "_now", lambda: now)
    monkeypatch.setattr(get_settings(), "monthly_coins", 100)
    await pool.execute(
        "INSERT INTO ai_requests(provider, model, input_tokens, output_tokens, created_at) VALUES"
        " ('anthropic', 'claude-sonnet-5-5', 10000, 1000, $1),"  # 30 coins
        " ('anthropic', 'claude-haiku-5-5', 100000, 10000, $2),"  # 15 coins
        " ('anthropic', 'claude-sonnet-5-5', 99999, 9999, $3)",  # previous month: not counted
        datetime(2026, 11, 2, 10, 0, tzinfo=UTC), now - timedelta(minutes=30), datetime(2026, 10, 31, 18, 0, tzinfo=UTC),
    )
    coins = (await client.get("/api/dashboard")).json()["coins"]
    assert coins["spent"] == 45.0 and coins["remaining"] == 55.0 and coins["percent"] == 45.0
    assert coins["exhausted"] is False and coins["days_in_month"] == 30
    # The pace counts from the first request of the month (Nov 2 10:00 UTC), not from Nov 1:
    # 45 coins / 8.4167 days = 5.35/day. Projection = spent + pace * days left (19.9583 d to Dec 1 00:00
    # local) = 45 + 106.71 = 151.71. The limit runs out (100 - 45) / 5.3465 = 10.29 days from now,
    # i.e. 2026-11-21 08:53 local.
    assert coins["avg_per_day"] == 5.35 and coins["projected_month"] == 151.71
    assert coins["runs_out_on"] == "2026-11-21"
    assert coins["unpriced_models"] == []
    assert len(coins["days"]) == 11 and coins["days"][1] == {"date": "2026-11-02", "coins": 30.0}
    assert coins["days"][-1] == {"date": "2026-11-11", "coins": 15.0}
    assert coins["by_model"] == [{"model": "claude-sonnet-5-5", "coins": 30.0}, {"model": "claude-haiku-5-5", "coins": 15.0}]


async def test_coin_forecast_waits_for_a_full_day(pool, monkeypatch):
    from iler import usage
    from iler.config import get_settings

    monkeypatch.setattr(get_settings(), "monthly_coins", 100)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    # the bot started 6 hours ago (installed mid-month): 30 coins so far, by an unpriced model too
    await pool.execute(
        "INSERT INTO ai_requests(provider, model, input_tokens, output_tokens, created_at) VALUES"
        " ('anthropic', 'claude-sonnet-5-5', 5000, 500, $1), ('openai', 'gpt-x-unknown', 5000, 500, $2)",
        now - timedelta(hours=6), now - timedelta(hours=1),
    )
    u = await usage.month_usage(now)
    assert u.spent == 30.0
    assert u.runs_out_on is None  # less than a day of data: no date yet
    assert u.avg_per_day == 30.0  # what was spent counts as one day's pace, not 30 / 0.25 days
    assert u.unpriced_models == ["gpt-x-unknown"]
    later = await usage.month_usage(now + timedelta(hours=18))  # a full day since the first request
    assert later.avg_per_day == 30.0 and later.runs_out_on == date(2026, 11, 23)


async def test_coin_alerts_once_per_level(pool, monkeypatch):
    from iler import usage
    from iler.config import get_settings
    from iler.engine import sender

    sent = []

    async def fake_notify(text, *, key=None):
        sent.append((key, text))
        return []

    monkeypatch.setattr(sender, "notify_admins", fake_notify)
    monkeypatch.setattr(get_settings(), "monthly_coins", 10)
    await pool.execute("INSERT INTO ai_requests(provider, model, input_tokens, output_tokens) VALUES"
                       " ('anthropic', 'claude-sonnet-5-5', 4000, 100)")  # 9 coins = 90%
    await usage.after_spend()
    await usage.after_spend()
    assert len(sent) == 1 and "90%" in sent[0][1]
    assert not await usage.limit_reached()
    await pool.execute("INSERT INTO ai_requests(provider, model, input_tokens, output_tokens) VALUES"
                       " ('anthropic', 'claude-sonnet-5-5', 1000, 0)")  # +2 coins
    await usage.after_spend()
    assert len(sent) == 2 and "исчерпан" in sent[1][1]
    assert await usage.limit_reached()
    usage.invalidate()


async def _put_spend(pool, coins: float) -> None:
    # claude-sonnet-5-5: $2 per 1M input tokens, so 500 input tokens = 1 coin
    await pool.execute("INSERT INTO ai_requests(provider, model, input_tokens, output_tokens)"
                       " VALUES ('anthropic', 'claude-sonnet-5-5', $1, 0)", round(coins * 500))


async def test_coin_alert_compares_unrounded_ratio(pool, monkeypatch):
    from iler import usage
    from iler.config import get_settings
    from iler.engine import sender

    sent = []

    async def fake_notify(text, *, key=None):
        sent.append(key)
        return []

    monkeypatch.setattr(sender, "notify_admins", fake_notify)
    monkeypatch.setattr(get_settings(), "monthly_coins", 10000)
    await _put_spend(pool, 9999.6)  # 99.996%: shows as 100.0% but the limit is not used up
    await usage.after_spend()
    month = f"{usage.month_start(datetime.now(UTC), usage._tz()):%Y-%m}"
    assert sent == [f"coins:{month}:10000:80"]
    assert not await usage.limit_reached()
    usage.invalidate()


async def test_coin_alerts_rearm_when_limit_changes(pool, monkeypatch):
    from iler import usage
    from iler.config import get_settings
    from iler.engine import sender

    sent = []

    async def fake_notify(text, *, key=None):
        sent.append(text)
        return []

    monkeypatch.setattr(sender, "notify_admins", fake_notify)
    monkeypatch.setattr(get_settings(), "monthly_coins", 10)
    await _put_spend(pool, 10)  # 10 of 10: exhausted
    await usage.after_spend()
    assert len(sent) == 1 and "исчерпан" in sent[0]
    monkeypatch.setattr(get_settings(), "monthly_coins", 20)  # ILER raised the limit: 50%
    await usage.after_spend()
    assert len(sent) == 1
    await _put_spend(pool, 7)  # 17 of 20 = 85%: the 80% alert fires again for the new limit
    await usage.after_spend()
    assert len(sent) == 2 and "85%" in sent[1]
    await _put_spend(pool, 3)  # 20 of 20
    await usage.after_spend()
    assert len(sent) == 3 and "исчерпан" in sent[2]
    usage.invalidate()


async def test_coin_alert_retried_until_delivered(pool, monkeypatch):
    from iler import settings_store, usage
    from iler.config import get_settings
    from iler.engine import sender
    from iler.settings_models import NotifySettings

    await settings_store.put("notify", NotifySettings(admin_phones=["77001112233"]))
    results = [["failed"], ["sent"]]
    calls = []

    async def fake_notify(text, *, key=None):
        calls.append(key)
        return results.pop(0) if results else ["sent"]

    monkeypatch.setattr(sender, "notify_admins", fake_notify)
    monkeypatch.setattr(get_settings(), "monthly_coins", 10)
    await _put_spend(pool, 9)
    await usage.after_spend()  # WhatsApp did not take it: not marked as sent
    await usage.after_spend()  # retried, delivered
    await usage.after_spend()  # nothing more to send
    assert len(calls) == 2 and calls[0] == calls[1]
    state = await settings_store.get("usage_alerts", usage.UsageAlerts)
    assert state.sent == [80] and state.limit == 10
    usage.invalidate()


async def test_test_chat_refused_when_coins_exhausted(client, monkeypatch):
    from iler import usage

    await login(client)

    async def reached():
        return True

    monkeypatch.setattr(usage, "limit_reached", reached)
    from iler.settings_models import BotSettings
    from iler import settings_store

    await settings_store.put("bot", BotSettings(prompt="Ты бот."))
    r = await client.post("/api/test-chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=csrf(client))
    assert r.status_code == 409 and r.json()["error"]["code"] == "coins_exhausted"


def test_coin_prices_and_overrides(monkeypatch):
    from iler import usage
    from iler.config import get_settings

    assert usage.coins_for("claude-haiku-5-5", 1_000_000, 0) == 100.0  # $0.10 = 100 coins
    assert usage.price_for("some-unknown-model") == usage.FALLBACK_PRICE
    monkeypatch.setattr(get_settings(), "model_prices", '{"gpt-5.4-mini": [0.4, 1.6]}')
    assert usage.price_for("gpt-5.4-mini") == (0.4, 1.6)
    monkeypatch.setattr(get_settings(), "model_prices", "not json")
    assert usage.price_for("gpt-5.4-mini") == usage.FALLBACK_PRICE


# --- writes go through the engine (AD-5) ----------------------------------------------------------

async def test_reading_unread_conversation_resets_once_via_store(client, pool):
    await login(client)
    conv = await make_conv(pool, "77010000001", unread=3)
    await add_msg(pool, conv, "Привет")
    async with events.subscription() as q:
        r = await client.get(f"/api/conversations/{conv}/messages")
        assert r.status_code == 200
        got = [q.get_nowait() for _ in range(q.qsize())]
        assert len(got) == 1 and got[0]["type"] == "conversation.updated"
        assert got[0]["data"]["conversation"]["id"] == conv and got[0]["data"]["conversation"]["unread"] == 0
        r = await client.get(f"/api/conversations/{conv}/messages")  # nothing to reset: no event
        assert r.status_code == 200 and q.empty()
    assert await pool.fetchval("SELECT unread FROM conversations WHERE id = $1", conv) == 0


def test_api_has_no_direct_conversation_writes():
    import re
    from pathlib import Path

    import iler.api

    write = re.compile(r"(?i)\b(update|insert\s+into|delete\s+from)\s+(public\.)?conversations\b")
    api_dir = Path(iler.api.__file__).parent
    offenders = [p.name for p in api_dir.rglob("*.py") if write.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
