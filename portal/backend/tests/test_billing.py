import asyncpg
import pytest

from nox_portal import mailer, tariffs
from tests.conftest import REG, csrf, register_verified, staff_login, unseal

KEY = "sk-ant-api03-" + "C" * 40


async def _choose(c, code="business"):
    return await c.post("/api/me/tariff", headers=csrf(c), json={"tariff": code})


async def _awaiting(client, other, pool, code="business") -> int:
    await register_verified(other)
    r = await _choose(other, code)
    assert r.status_code == 200, r.text
    await staff_login(client, pool)
    return await pool.fetchval("SELECT id FROM accounts")


async def test_tariff_list_is_public(client):
    r = await client.get("/api/tariffs")
    assert r.status_code == 200
    assert [t["code"] for t in r.json()] == [t.code for t in tariffs.TARIFFS]
    assert all(t["price_kzt"] > 0 and t["coins"] > 0 for t in r.json())


async def test_choose_tariff_issues_invoice_and_mails_it(other, pool):
    await register_verified(other)
    mailer.OUTBOX.clear()
    r = await _choose(other)
    assert r.status_code == 200, r.text
    me = r.json()
    business = tariffs.get("business")
    assert me["status"] == "awaiting_payment"
    assert me["tariff"] == "business" and me["invoice_amount"] == business.price_kzt
    assert me["invoice_no"].startswith("NOX-") and me["invoice_no"].endswith("-00001")
    letters = {m.to: m for m in mailer.OUTBOX}
    assert "Счёт NOX-" in letters[REG["email"]].subject
    assert "39 000 ₸" in letters[REG["email"]].text and "/invoice" in letters[REG["email"]].text
    assert "Выставлен счёт" in letters["staff@example.kz"].subject

    inv = (await other.get("/api/me/invoice")).json()
    assert inv["number"] == me["invoice_no"] and inv["amount_kzt"] == business.price_kzt
    assert inv["seller"]["name"] == "Nox service"
    assert inv["buyer_company"] == REG["company"] and inv["bot_host"] == "primer.example.kz"
    assert inv["paid"] is False


async def test_switch_tariff_keeps_invoice_number(other, pool):
    await register_verified(other)
    first = (await _choose(other, "start")).json()
    second = (await _choose(other, "pro")).json()
    assert first["invoice_no"] == second["invoice_no"]
    assert second["invoice_amount"] == tariffs.get("pro").price_kzt


async def test_tariff_rules(client, other, pool):
    assert (await other.post("/api/me/tariff", json={"tariff": "start"})).status_code == 401
    await register_verified(other)
    assert (await other.post("/api/me/tariff", json={"tariff": "start"})).status_code == 403  # no CSRF
    assert (await _choose(other, "gold")).status_code == 422
    assert (await other.get("/api/me/invoice")).status_code == 404
    await pool.execute("UPDATE accounts SET status = 'approved'")
    assert (await _choose(other)).status_code == 409


async def test_mark_paid_records_payment_and_queues_bot(client, other, internal, pool):
    aid = await _awaiting(client, other, pool)
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client),
                          json={"amount_kzt": 39000, "reference": "п/п 123", "anthropic_key": KEY})
    assert r.status_code == 200, r.text
    pay = await pool.fetchrow("SELECT * FROM payments")
    assert (pay["amount_kzt"], pay["tariff"], pay["reference"], pay["method"]) == (39000, "business", "п/п 123", "invoice")
    assert pay["invoice_no"] == await pool.fetchval("SELECT invoice_no FROM accounts")
    assert await pool.fetchval("SELECT status FROM accounts") == "approved"
    assert await pool.fetchval("SELECT coins_limit FROM accounts") == tariffs.get("business").coins
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["coins"] == 100000 and unseal(job["llm_key_sealed"]) == KEY
    detail = (await client.get(f"/api/staff/applications/{aid}")).json()
    assert detail["payments"][0]["amount_kzt"] == 39000 and detail["payments"][0]["staff_email"] == "ops@example.kz"
    assert detail["tariff"] == "business" and detail["invoice_amount"] == 39000
    actions = [a["action"] for a in detail["audit"]]
    assert "staff.mark_paid" in actions and "staff.approve" in actions
    assert (await other.get("/api/me/invoice")).json()["paid"] is True
    staff_inv = (await client.get(f"/api/staff/applications/{aid}/invoice")).json()
    assert staff_inv["paid"] is True
    # paid twice is refused (wrong status)
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client), json={"amount_kzt": 39000})
    assert r.status_code == 409
    assert await pool.fetchval("SELECT count(*) FROM payments") == 1


async def test_mark_paid_coin_override_and_validation(client, other, pool):
    aid = await _awaiting(client, other, pool, "start")
    url = f"/api/staff/applications/{aid}/mark-paid"
    assert (await client.post(url, headers=csrf(client), json={"amount_kzt": 0})).status_code == 422
    assert (await client.post(url, headers=csrf(client), json={"amount_kzt": -5})).status_code == 422
    r = await client.post(url, headers=csrf(client), json={"amount_kzt": 15000, "coins": 50000})
    assert r.status_code == 200
    assert await pool.fetchval("SELECT coins_limit FROM accounts") == 50000


async def test_mark_paid_only_when_awaiting_payment(client, other, pool):
    await register_verified(other)
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client), json={"amount_kzt": 1})
    assert r.status_code == 409


async def test_failed_slug_change_rolls_back_payment(client, other, pool, app):
    from httpx import ASGITransport, AsyncClient

    aid = await _awaiting(client, other, pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://bot.example.kz") as third:
        await register_verified(third, email="c@example.kz", slug="taken")
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client),
                          json={"amount_kzt": 39000, "slug": "taken"})
    assert r.status_code == 409
    assert await pool.fetchval("SELECT count(*) FROM payments") == 0
    assert await pool.fetchval("SELECT status FROM accounts WHERE id = $1", aid) == "awaiting_payment"


async def test_payments_ledger_is_append_only(client, other, pool):
    aid = await _awaiting(client, other, pool)
    await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client), json={"amount_kzt": 39000})
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await pool.execute("UPDATE payments SET amount_kzt = 1")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await pool.execute("DELETE FROM payments")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await pool.execute("TRUNCATE payments")
    # a paid account cannot be deleted from under its ledger rows
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute("DELETE FROM accounts WHERE id = $1", aid)


async def test_approve_without_payment_still_possible_and_audited(client, other, pool):
    aid = await _awaiting(client, other, pool)
    r = await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client), json={"coins": 30000})
    assert r.status_code == 200
    audit = await pool.fetchrow("SELECT details FROM audit_log WHERE action = 'staff.approve'")
    assert audit["details"]["payment"] is None
    assert await pool.fetchval("SELECT count(*) FROM payments") == 0


async def test_reject_while_awaiting_payment(client, other, pool):
    aid = await _awaiting(client, other, pool)
    r = await client.post(f"/api/staff/applications/{aid}/reject", headers=csrf(client), json={"reason": "Нет оплаты"})
    assert r.status_code == 200
    assert await pool.fetchval("SELECT status FROM accounts") == "rejected"


async def test_staff_config_lists_tariffs(client, pool):
    await staff_login(client, pool)
    cfg = (await client.get("/api/staff/config")).json()
    assert [t["code"] for t in cfg["tariffs"]] == ["start", "business", "pro"]
