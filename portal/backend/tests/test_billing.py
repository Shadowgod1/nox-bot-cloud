"""Tariff choice, invoices issued by a manager, the payments ledger, the Free test tariff."""

from datetime import datetime, timedelta

import asyncpg
import pytest

from nox_portal import mailer, renewal, tariffs
from nox_portal.config import get_settings
from tests.conftest import REG, csrf, register_verified, staff_login, unseal

KEY = "sk-ant-api03-" + "C" * 40


async def _choose(c, code="business", method="invoice"):
    return await c.post("/api/me/tariff", headers=csrf(c), json={"tariff": code, "method": method})


async def _issue(client, aid, **body):
    return await client.post(f"/api/staff/applications/{aid}/issue-invoice", headers=csrf(client),
                             json={"kind": "subscription", **body})


async def _awaiting(client, other, pool, code="business") -> int:
    """register -> tariff by invoice -> staff issues the invoice: awaiting_payment."""
    await register_verified(other)
    r = await _choose(other, code)
    assert r.status_code == 200, r.text
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    r = await _issue(client, aid)
    assert r.status_code == 200, r.text
    return aid


# ---- tariff list ----------------------------------------------------------------------------


async def test_tariff_list_paid_then_free(client):
    r = await client.get("/api/tariffs")
    assert r.status_code == 200
    codes = [t["code"] for t in r.json()]
    assert codes == [t.code for t in tariffs.TARIFFS] + ["free"]
    assert all(t["price_kzt"] > 0 and t["coins"] > 0 for t in r.json()[:-1])
    free = r.json()[-1]
    assert (free["name"], free["price_kzt"], free["coins"], free["available"]) == ("Free (тест)", 0, 2000, True)
    assert free["description"] == "Пробный тариф для теста бота"


async def test_free_hidden_when_not_public(client, other, pool, monkeypatch):
    monkeypatch.setattr(get_settings(), "free_tariff_public", False)
    assert "free" not in [t["code"] for t in (await client.get("/api/tariffs")).json()]
    await register_verified(other)
    assert (await _choose(other, "free")).status_code == 422
    # staff still can assign it
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    r = await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client), json={"tariff": "free"})
    assert r.status_code == 200, r.text
    acc = await pool.fetchrow("SELECT status, tariff, coins_limit, free_until FROM accounts")
    assert (acc["status"], acc["tariff"], acc["coins_limit"]) == ("approved", "free", 2000)
    assert acc["free_until"] is not None


# ---- invoice through a manager --------------------------------------------------------------


async def test_choose_by_invoice_issues_nothing_and_notifies_staff(other, pool):
    await register_verified(other)
    mailer.OUTBOX.clear()
    r = await _choose(other)
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["status"] == "awaiting_invoice" and me["tariff"] == "business" and me["pay_method"] == "invoice"
    assert me["invoice_no"] is None and me["invoice_amount"] is None
    assert await pool.fetchval("SELECT count(*) FROM invoices") == 0
    assert [m.to for m in mailer.OUTBOX] == ["staff@example.kz"]
    assert "выбрал оплату по счёту" in mailer.OUTBOX[0].subject
    assert "/staff/applications/" in mailer.OUTBOX[0].text
    assert (await other.get("/api/me/invoice")).status_code == 404


async def test_staff_issues_invoice_with_amount_and_comment(client, other, pool):
    await register_verified(other)
    await _choose(other)
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    mailer.OUTBOX.clear()
    r = await _issue(client, aid, amount_kzt=90000, comment="скидка 10% по договорённости")
    assert r.status_code == 200, r.text
    number = r.json()["number"]
    assert number.startswith("NOX-") and number.endswith("-00001") and r.json()["amount_kzt"] == 90000
    acc = await pool.fetchrow("SELECT status, invoice_no, invoice_amount, invoice_note FROM accounts")
    assert (acc["status"], acc["invoice_no"], acc["invoice_amount"]) == ("awaiting_payment", number, 90000)
    letter = mailer.OUTBOX[-1]
    assert letter.to == REG["email"] and f"Счёт {number}" in letter.subject
    assert "90 000 ₸" in letter.text and "скидка 10%" in letter.text and "/invoice" in letter.text
    inv = (await other.get("/api/me/invoice")).json()
    assert inv["number"] == number and inv["amount_kzt"] == 90000 and inv["comment"] == "скидка 10% по договорённости"
    assert inv["seller"]["name"] == "Nox service" and inv["paid"] is False
    assert inv["buyer_company"] == REG["company"] and inv["bot_host"] == "primer.example.kz"
    # default amount = tariff price
    r = await _issue(client, aid)
    assert r.json()["number"] == number and r.json()["amount_kzt"] == tariffs.get("business").price_kzt


async def test_issue_invoice_rules(client, other, pool):
    await register_verified(other)
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    assert (await _issue(client, aid)).status_code == 422, "no tariff chosen yet"
    assert (await _issue(client, aid, tariff="free")).status_code == 422
    assert (await _issue(client, aid, tariff="start")).status_code == 200, "staff may pick the tariff"
    assert (await _issue(client, aid, amount_kzt=0)).status_code == 422
    assert (await client.post(f"/api/staff/applications/{aid}/issue-invoice", json={"kind": "subscription"})
            ).status_code == 403
    await pool.execute("UPDATE accounts SET status = 'active'")
    assert (await _issue(client, aid)).status_code == 409
    assert (await _issue(client, aid, kind="renewal")).status_code == 409, "no period yet"
    assert (await _issue(client, aid, kind="topup")).status_code == 409


async def test_change_choice_voids_issued_invoice_keeps_number(client, other, pool):
    aid = await _awaiting(client, other, pool, "start")
    first = await pool.fetchval("SELECT invoice_no FROM accounts")
    me = (await _choose(other, "pro")).json()
    assert me["status"] == "awaiting_invoice" and me["invoice_amount"] is None
    assert (await other.get("/api/me/invoice")).status_code == 404
    assert (await other.get("/api/me/invoices")).json() == []
    r = await _issue(client, aid)
    assert r.json()["number"] == first and r.json()["amount_kzt"] == tariffs.get("pro").price_kzt


async def test_tariff_rules(client, other, pool):
    assert (await other.post("/api/me/tariff", json={"tariff": "start"})).status_code == 401
    await register_verified(other)
    assert (await other.post("/api/me/tariff", json={"tariff": "start"})).status_code == 403  # no CSRF
    assert (await _choose(other, "gold")).status_code == 422
    assert (await _choose(other, "start", "cash")).status_code == 422
    assert (await _choose(other, "start", "online")).status_code == 409, "no online provider configured"
    await pool.execute("UPDATE accounts SET status = 'approved', tariff = 'start'")
    assert (await _choose(other)).status_code == 409


async def test_mark_paid_records_payment_and_queues_bot(client, other, internal, pool):
    aid = await _awaiting(client, other, pool)
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client),
                          json={"amount_kzt": 89990, "reference": "п/п 123", "anthropic_key": KEY})
    assert r.status_code == 200, r.text
    pay = await pool.fetchrow("SELECT * FROM payments")
    assert (pay["amount_kzt"], pay["tariff"], pay["reference"], pay["method"]) == (89990, "business", "п/п 123", "invoice")
    assert pay["invoice_no"] == await pool.fetchval("SELECT invoice_no FROM accounts")
    assert await pool.fetchval("SELECT status FROM accounts") == "approved"
    assert await pool.fetchval("SELECT coins_limit FROM accounts") == tariffs.get("business").coins
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["coins"] == 60000 and unseal(job["llm_key_sealed"]) == KEY
    detail = (await client.get(f"/api/staff/applications/{aid}")).json()
    assert detail["payments"][0]["amount_kzt"] == 89990 and detail["payments"][0]["staff_email"] == "ops@example.kz"
    assert detail["tariff"] == "business" and detail["invoice_amount"] == 89990
    actions = [a["action"] for a in detail["audit"]]
    assert "staff.mark_paid" in actions and "staff.approve" in actions and "staff.invoice_issued" in actions
    assert (await other.get("/api/me/invoice")).json()["paid"] is True
    staff_inv = (await client.get(f"/api/staff/applications/{aid}/invoice")).json()
    assert staff_inv["paid"] is True
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client), json={"amount_kzt": 89990})
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
    await _choose(other)
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client), json={"amount_kzt": 1})
    assert r.status_code == 409, "awaiting_invoice: issue the invoice first"


async def test_failed_slug_change_rolls_back_payment(client, other, pool, app):
    from httpx import ASGITransport, AsyncClient

    aid = await _awaiting(client, other, pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://bot.example.kz") as third:
        await register_verified(third, email="c@example.kz", slug="taken", phone="+7 702 000 00 00")
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client),
                          json={"amount_kzt": 89990, "slug": "taken"})
    assert r.status_code == 409
    assert await pool.fetchval("SELECT count(*) FROM payments") == 0
    assert await pool.fetchval("SELECT status FROM accounts WHERE id = $1", aid) == "awaiting_payment"


async def test_payments_ledger_is_append_only(client, other, pool):
    aid = await _awaiting(client, other, pool)
    await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client), json={"amount_kzt": 89990})
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await pool.execute("UPDATE payments SET amount_kzt = 1")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await pool.execute("DELETE FROM payments")
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await pool.execute("TRUNCATE payments CASCADE")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute("DELETE FROM accounts WHERE id = $1", aid)


async def test_approve_without_payment_uses_tariff_coins(client, other, pool):
    await register_verified(other)
    await _choose(other, "start")
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    r = await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client), json={})
    assert r.status_code == 200, r.text
    audit = await pool.fetchrow("SELECT details FROM audit_log WHERE action = 'staff.approve'")
    assert audit["details"]["payment"] is None and audit["details"]["coins"] == tariffs.get("start").coins
    assert await pool.fetchval("SELECT count(*) FROM payments") == 0


async def test_reject_while_awaiting_invoice(client, other, pool):
    await register_verified(other)
    await _choose(other)
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    r = await client.post(f"/api/staff/applications/{aid}/reject", headers=csrf(client), json={"reason": "Нет оплаты"})
    assert r.status_code == 200
    assert await pool.fetchval("SELECT status FROM accounts") == "rejected"


async def test_staff_list_filters_new_statuses(client, other, pool):
    await register_verified(other)
    await _choose(other)
    await staff_login(client, pool)
    rows = (await client.get("/api/staff/applications?status=awaiting_invoice")).json()
    assert len(rows) == 1 and rows[0]["status"] == "awaiting_invoice"
    assert (await client.get("/api/staff/applications?status=payment_pending")).json() == []
    assert (await client.get("/api/staff/counts")).json() == {"awaiting_invoice": 1}
    assert (await client.get("/api/staff/applications?status=awaiting_approval")).status_code == 422


async def test_old_style_awaiting_payment_account_still_works(client, other, pool, internal):
    """An account that got an automatic invoice before migration 0004 (pay_method NULL) is paid as before."""
    await register_verified(other)
    await pool.execute(
        "UPDATE accounts SET status = 'awaiting_payment', tariff = 'start', invoice_no = 'NOX-2026-00042',"
        " invoice_amount = 59990, invoice_issued_at = now() - interval '3 days'")
    me = (await other.get("/api/me")).json()
    assert me["status"] == "awaiting_payment" and me["pay_method"] is None
    inv = (await other.get("/api/me/invoice")).json()
    assert inv["number"] == "NOX-2026-00042" and inv["amount_kzt"] == 59990 and inv["comment"] == ""
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client), json={"amount_kzt": 59990})
    assert r.status_code == 200, r.text
    assert await pool.fetchval("SELECT invoice_no FROM payments") == "NOX-2026-00042"
    assert (await internal.post("/internal/jobs/claim")).json()["coins"] == tariffs.get("start").coins


async def test_staff_config_lists_tariffs(client, pool):
    await staff_login(client, pool)
    cfg = (await client.get("/api/staff/config")).json()
    assert [t["code"] for t in cfg["tariffs"]] == ["start", "business", "pro", "free"]


# ---- Free (test) ----------------------------------------------------------------------------


async def test_free_is_approved_at_once(client, other, pool, internal):
    await register_verified(other)
    mailer.OUTBOX.clear()
    r = await _choose(other, "free")
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["status"] == "approved" and me["tariff"] == "free" and me["pay_method"] == "free"
    assert me["invoice_no"] is None
    free_until = datetime.fromisoformat(me["free_until"])
    assert timedelta(days=6, hours=23) < free_until - datetime.now(free_until.tzinfo) <= timedelta(days=7)
    assert await pool.fetchval("SELECT count(*) FROM invoices") == 0
    assert await pool.fetchval("SELECT count(*) FROM payments") == 0
    audit = await pool.fetchrow("SELECT details FROM audit_log WHERE action = 'client.auto_free'")
    assert audit["details"]["payment"] is None and audit["details"]["coins"] == 2000
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["kind"] == "create" and job["coins"] == 2000 and job["gateway_key_sealed"]
    assert any("Free" in m.subject for m in mailer.OUTBOX if m.to == "staff@example.kz")
    # the trial grant
    assert await pool.fetchval("SELECT coins FROM coin_grants WHERE kind = 'monthly'") == 2000


async def test_free_once_per_email_and_phone(client, other, pool, app):
    from httpx import ASGITransport, AsyncClient

    await register_verified(other)
    assert (await _choose(other, "free")).status_code == 200
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://bot.example.kz") as second:
        # same phone written differently
        await register_verified(second, email="two@example.kz", slug="vtoroy", phone="8 (701) 000-00-00")
        r = await _choose(second, "free")
        assert r.status_code == 409 and r.json()["error"]["code"] == "free_used"
        assert "уже использован" in r.json()["error"]["message"]
        assert await pool.fetchval("SELECT status FROM accounts WHERE email = 'two@example.kz'") == "verified"
        # a paid tariff is still fine
        assert (await _choose(second, "start")).status_code == 200


async def test_free_global_cap(client, other, pool, monkeypatch):
    monkeypatch.setattr(get_settings(), "free_max_active", 1)
    await pool.execute(
        "INSERT INTO accounts(email, password_hash, company, contact_name, phone, slug, status, consent_at, tariff)"
        " VALUES ('x@example.kz', 'x', 'X', 'X', '+7 700 111 11 11', 'xx', 'active', now(), 'free')")
    assert (await client.get("/api/tariffs")).json()[-1]["available"] is False
    await register_verified(other)
    r = await _choose(other, "free")
    assert r.status_code == 409 and r.json()["error"]["code"] == "free_full"
    assert r.json()["error"]["message"] == "Тестовые места закончились, выберите тариф или напишите менеджеру"
    await pool.execute("UPDATE accounts SET status = 'suspended' WHERE email = 'x@example.kz'")
    assert (await _choose(other, "free")).status_code == 200, "suspended trials do not hold a place"


async def _free_active(other, pool, internal) -> int:
    await register_verified(other)
    assert (await _choose(other, "free")).status_code == 200
    job = (await internal.post("/internal/jobs/claim")).json()
    r = await internal.post(f"/internal/jobs/{job['id']}/result",
                            json={"ok": True, "port": 8110, "admin_url": "https://primer.example.kz"})
    assert r.status_code == 200, r.text
    return await pool.fetchval("SELECT id FROM accounts")


async def test_free_no_renewal_and_trial_end_suspends(client, other, pool, internal):
    aid = await _free_active(other, pool, internal)
    # the month is over too: no reminder, no rollover, no unpaid suspension for Free
    await pool.execute("UPDATE accounts SET period_end = now() - interval '10 days' WHERE id = $1", aid)
    mailer.OUTBOX.clear()
    out = await renewal.tick()
    assert (out["renewal_notices"], out["renewed"], out["suspended"], out["trials_ended"]) == (0, 0, 0, 0)
    assert [m for m in mailer.OUTBOX if "сводка" not in m.subject] == []
    await pool.execute("UPDATE accounts SET free_until = now() - interval '1 minute' WHERE id = $1", aid)
    out = await renewal.tick()
    assert out["trials_ended"] == 1
    acc = await pool.fetchrow("SELECT status, suspend_reason FROM accounts")
    assert (acc["status"], acc["suspend_reason"]) == ("suspended", "trial_ended")
    assert await pool.fetchval("SELECT kind FROM jobs WHERE status = 'pending'") == "suspend"
    client_mail = next(m for m in mailer.OUTBOX if m.to == REG["email"])
    assert "тестовый период закончился" in client_mail.subject
    assert "/cabinet" in client_mail.text
    assert (await renewal.tick())["trials_ended"] == 0


async def test_upgrade_free_by_invoice_keeps_bot(client, other, pool, internal):
    aid = await _free_active(other, pool, internal)
    await pool.execute("UPDATE accounts SET status = 'suspended', suspend_reason = 'trial_ended' WHERE id = $1", aid)
    mailer.OUTBOX.clear()
    me = (await _choose(other, "business")).json()
    assert me["status"] == "suspended" and me["upgrade_tariff"] == "business"
    assert "Переход с Free" in mailer.OUTBOX[0].subject
    assert (await _choose(other, "free")).status_code == 409
    await staff_login(client, pool)
    detail = (await client.get(f"/api/staff/applications/{aid}")).json()
    assert detail["upgrade_tariff"] == "business" and detail["suspend_reason"] == "trial_ended"
    r = await _issue(client, aid, kind="upgrade")
    assert r.status_code == 200, r.text
    number = r.json()["number"]
    assert r.json()["amount_kzt"] == tariffs.get("business").price_kzt
    assert f"Счёт {number}" in mailer.OUTBOX[-1].subject and mailer.OUTBOX[-1].to == REG["email"]
    inv = (await other.get(f"/api/me/invoices/{number}")).json()
    assert inv["kind"] == "upgrade" and inv["tariff"]["name"] == "Бизнес"
    inv_id = await pool.fetchval("SELECT id FROM invoices WHERE number = $1", number)
    r = await client.post(f"/api/staff/invoices/{inv_id}/mark-paid", headers=csrf(client), json={"amount_kzt": 89990})
    assert r.status_code == 200, r.text
    acc = await pool.fetchrow("SELECT tariff, coins_limit, free_until, suspend_reason, upgrade_tariff, slug"
                              " FROM accounts")
    assert (acc["tariff"], acc["coins_limit"], acc["free_until"], acc["suspend_reason"], acc["upgrade_tariff"]) == \
        ("business", 60000, None, None, None)
    assert await pool.fetchval("SELECT kind FROM jobs WHERE status = 'pending'") == "resume"
    assert await pool.fetchval("SELECT count(*) FROM jobs WHERE kind = 'create'") == 1, "same bot"
    bal = (await other.get("/api/me/usage")).json()["balance"]
    assert bal["monthly_coins"] == 60000
    assert any("тариф подключён" in m.subject for m in mailer.OUTBOX)


async def test_reissued_invoice_letter_has_requisites(client, other, pool):
    aid = await _awaiting(client, other, pool)
    assert aid
    assert "Получатель: Nox service" in [m for m in mailer.OUTBOX if m.to == REG["email"]][-1].text
