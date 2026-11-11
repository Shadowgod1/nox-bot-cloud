"""Builtin LLM tools (Task 7): create_booking_request, handover_to_operator."""

from datetime import UTC, datetime, timedelta

from engine_fakes import ADMIN_JID, ADMIN_PHONE, CLIENT_JID, env  # noqa: F401

from iler.settings_models import AllSettings, BotSettings, LlmSettings, NotifySettings, SenetSettings
from iler.tools import registry
from iler.tools.base import ToolContext


def _ctx(cid, *, dry_run=False, bot=None) -> ToolContext:
    return ToolContext(conversation_id=cid, phone="77010000001", settings=bot or BotSettings(),
                       now=datetime.now(UTC), dry_run=dry_run)


async def _conv(pool) -> int:
    return await pool.fetchval(
        "INSERT INTO conversations(chat_jid, phone, name) VALUES ($1, '77010000001', 'Айдар') RETURNING id",
        CLIENT_JID,
    )


def test_builtin_tools_registered_and_enabled():
    names = {t.name for t in registry.enabled_tools(
        AllSettings(bot=BotSettings(), llm=LlmSettings(), notify=NotifySettings(), senet=SenetSettings())
    )}
    assert {"create_booking_request", "handover_to_operator"} <= names
    assert "get_club_info" not in names  # removed: the business prompt carries all business info
    tool = registry.get("create_booking_request")
    assert set(tool.parameters["required"]) == {"date", "time", "duration_hours", "seats"}


async def test_handover_sets_status_and_pauses(env, pool):
    await env.set_notify(admin_phones=[ADMIN_PHONE])
    cid = await _conv(pool)
    bot = BotSettings(handover_pause_minutes=60)
    res = await registry.get("handover_to_operator").run(_ctx(cid, bot=bot), {"reason": "жалоба на ПК"})
    assert res.content["status"] == "handed_over"
    conv = await pool.fetchrow("SELECT * FROM conversations WHERE id = $1", cid)
    assert conv["status"] == "needs_operator"
    assert timedelta(minutes=59) < conv["paused_until"] - datetime.now(UTC) <= timedelta(minutes=60, seconds=5)
    admin_texts = env.gw.texts(ADMIN_JID)
    assert len(admin_texts) == 1 and "жалоба на ПК" in admin_texts[0]
    assert env.gw.texts(CLIENT_JID) == []


async def test_handover_dry_run_changes_nothing(env, pool):
    cid = await _conv(pool)
    res = await registry.get("handover_to_operator").run(_ctx(cid, dry_run=True), {"reason": "x"})
    assert res.content["status"] == "handed_over"
    assert await pool.fetchval("SELECT status FROM conversations") == "active"
    assert env.gw.sent == []


async def test_booking_tool_dry_run_simulated(env, pool):
    res = await registry.get("create_booking_request").run(
        _ctx(None, dry_run=True), {"date": "2026-11-12", "time": "18:00", "duration_hours": 3, "seats": 2}
    )
    assert res.content["status"] == "simulated"
    assert await pool.fetchval("SELECT count(*) FROM booking_requests") == 0
    assert env.gw.sent == []


async def test_booking_tool_creates_pending_request(env, pool):
    cid = await _conv(pool)
    res = await registry.get("create_booking_request").run(
        _ctx(cid), {"date": "2026-11-12", "time": "18:00", "duration_hours": 3, "seats": 2, "zone": "VIP"}
    )
    row = await pool.fetchrow("SELECT * FROM booking_requests")
    assert row["status"] == "pending" and row["conversation_id"] == cid
    assert row["payload"]["zone"] == "VIP"
    assert res.content["request_id"] == row["id"] and res.content["status"] == "pending"


async def test_booking_tool_validates_args(env, pool):
    cid = await _conv(pool)
    res = await registry.get("create_booking_request").run(_ctx(cid), {"date": "2026-11-12", "seats": 2})
    assert isinstance(res.content, str) and res.content.startswith("error:")
    assert await pool.fetchval("SELECT count(*) FROM booking_requests") == 0
