"""Online payments with the mock provider (create -> signed webhook -> what «Отметить оплату» does), webhook
security and idempotency, and migration 0004 on a database with pre-0004 data."""

import json
import shutil

import asyncpg
import pytest

from nox_portal import mailer, ratelimit, tariffs
from nox_portal.config import get_settings
from nox_portal.db import MIGRATIONS_DIR, migrate
from nox_portal.payments import mock
from tests.conftest import ADMIN_DSN, REG, _base, csrf, register_verified, staff_login
from tests.gw import login, make_account

HOOK = "/api/payments/webhook/mock"


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(get_settings(), "payment_provider", "mock")
    monkeypatch.setattr(get_settings(), "payment_mock_allowed", True)


def _ppid(url: str) -> str:
    return url.rsplit("/", 1)[1]


async def _webhook(c, ppid: str, status: str = "succeeded", amount: int | None = None, *, raw: bytes | None = None,
                   signature: str | None = None):
    if raw is None:
        op_amount = amount
        raw = mock.payload(ppid, status, op_amount if op_amount is not None else 0)
    return await c.post(HOOK, content=raw, headers={"content-type": "application/json",
                                                    mock.SIGNATURE_HEADER: signature or mock.sign(raw)})


async def _sign_via_page(c, ppid: str, result: str = "succeeded"):
    """What the mock checkout page does: ask the api to sign, then post the webhook."""
    r = await c.post(f"/api/payments/mock/{ppid}/sign", headers=csrf(c), json={"result": result})
    assert r.status_code == 200, r.text
    data = r.json()
    return await c.post(data["webhook_url"], content=data["payload"].encode(),
                        headers={"content-type": "application/json", data["signature_header"]: data["signature"]})


async def _choose_online(c, code="start"):
    r = await c.post("/api/me/tariff", headers=csrf(c), json={"tariff": code, "method": "online"})
    assert r.status_code == 200, r.text
    return r.json()


# ---- configuration --------------------------------------------------------------------------


async def test_online_hidden_by_default_and_mock_needs_permission(client, monkeypatch):
    cfg = (await client.get("/api/public-config")).json()
    assert cfg["online_payments"] is False and cfg["online_methods"] == []
    monkeypatch.setattr(get_settings(), "payment_provider", "mock")
    assert (await client.get("/api/public-config")).json()["online_payments"] is False, \
        "mock refused: e-mail mode is not log and NOX_PAYMENT_MOCK_ALLOWED is off"
    monkeypatch.setattr(get_settings(), "payment_mock_allowed", True)
    cfg = (await client.get("/api/public-config")).json()
    assert cfg["online_payments"] is True and cfg["online_methods"] == ["card", "kaspi_qr"]
    monkeypatch.setattr(get_settings(), "payment_mock_allowed", False)
    monkeypatch.setattr(get_settings(), "email_mode", "log")
    assert (await client.get("/api/public-config")).json()["online_payments"] is True


# ---- subscription ---------------------------------------------------------------------------


async def test_online_subscription_end_to_end(client, other, pool, internal, online):
    await register_verified(other)
    me = await _choose_online(other)
    assert me["status"] == "payment_pending" and me["pay_method"] == "online"
    assert me["payment_url"].startswith("https://bot.example.kz/pay/mock/mock_")
    ppid = _ppid(me["payment_url"])
    page = (await other.get(f"/api/payments/mock/{ppid}")).json()
    assert page["amount_kzt"] == 59990 and page["status"] == "pending" and page["purpose_ru"] == "Подключение"
    mailer.OUTBOX.clear()
    r = await _sign_via_page(other, ppid)
    assert r.status_code == 200 and r.json()["outcome"] == "applied", r.text
    acc = await pool.fetchrow("SELECT status, tariff, coins_limit, invoice_no, invoice_amount FROM accounts")
    assert (acc["status"], acc["tariff"], acc["coins_limit"]) == ("approved", "start", tariffs.get("start").coins)
    assert acc["invoice_no"].startswith("NOX-") and acc["invoice_amount"] == 59990
    pay = await pool.fetchrow("SELECT * FROM payments")
    assert (pay["method"], pay["amount_kzt"], pay["reference"], pay["staff_id"]) == ("online:mock", 59990, ppid, None)
    assert pay["invoice_no"] == acc["invoice_no"]
    assert await pool.fetchval("SELECT status FROM online_payments") == "succeeded"
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["kind"] == "create" and job["coins"] == 30000 and job["gateway_key_sealed"]
    assert any("Онлайн-оплата" in m.subject for m in mailer.OUTBOX if m.to == "staff@example.kz")
    assert (await other.get("/api/me/invoice")).json()["paid"] is True
    assert (await other.get("/api/me")).json()["payment_url"] is None
    ev = await pool.fetchrow("SELECT * FROM payment_events")
    assert ev["outcome"] == "applied" and len(ev["payload_sha256"]) == 64 and ev["status"] == "succeeded"

    # the provider retries: nothing happens twice
    r = await _webhook(other, ppid, amount=59990)
    assert r.status_code == 200 and r.json()["outcome"] == "duplicate"
    assert await pool.fetchval("SELECT count(*) FROM payments") == 1
    assert await pool.fetchval("SELECT count(*) FROM jobs WHERE kind = 'create'") == 1
    assert await pool.fetchval("SELECT count(*) FROM payment_events") == 2
    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await pool.execute("DELETE FROM payment_events")


async def test_bad_signature_401_changes_nothing(client, other, pool, online):
    await register_verified(other)
    ppid = _ppid((await _choose_online(other))["payment_url"])
    raw = mock.payload(ppid, "succeeded", 59990)
    r = await _webhook(client, ppid, raw=raw, signature="0" * 64)
    assert r.status_code == 401
    r = await client.post(HOOK, content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 401
    assert await pool.fetchval("SELECT status FROM accounts") == "payment_pending"
    assert await pool.fetchval("SELECT count(*) FROM payments") == 0
    assert await pool.fetchval("SELECT count(*) FROM payment_events") == 0
    assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE action = 'payment.webhook_rejected'") == 2
    # a provider that is not configured has no webhook
    assert (await client.post("/api/payments/webhook/kaspi", content=b"{}")).status_code == 404


async def test_failed_webhook_leaves_status(client, other, pool, online):
    await register_verified(other)
    ppid = _ppid((await _choose_online(other))["payment_url"])
    r = await _sign_via_page(other, ppid, "failed")
    assert r.json()["outcome"] == "applied"
    assert await pool.fetchval("SELECT status FROM accounts") == "payment_pending"
    assert await pool.fetchval("SELECT status FROM online_payments") == "failed"
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
    assert (await other.get("/api/me")).json()["payment_url"] is None
    # the client switches to an invoice
    r = await other.post("/api/me/tariff", headers=csrf(other), json={"tariff": "start", "method": "invoice"})
    assert r.json()["status"] == "awaiting_invoice"


async def test_amount_mismatch_and_unknown_payment(client, other, pool, online):
    await register_verified(other)
    ppid = _ppid((await _choose_online(other))["payment_url"])
    mailer.OUTBOX.clear()
    r = await _webhook(client, ppid, amount=100)
    assert r.json()["outcome"] == "amount_mismatch"
    assert await pool.fetchval("SELECT status FROM accounts") == "payment_pending"
    assert await pool.fetchval("SELECT count(*) FROM payments") == 0
    assert any("требует внимания" in m.subject for m in mailer.OUTBOX)
    r = await _webhook(client, "mock_999_nothing", amount=59990)
    assert r.status_code == 200 and r.json()["outcome"] == "unknown_payment"


async def test_webhook_rate_limit(client, online, monkeypatch, pool):
    monkeypatch.setitem(ratelimit.LIMITS, "payhook:ip", (2, 60))
    for _ in range(2):
        assert (await _webhook(client, "mock_x", amount=1)).status_code == 200
    assert (await _webhook(client, "mock_x", amount=1)).status_code == 429


async def test_mock_page_belongs_to_the_client(client, other, pool, online, app):
    from httpx import ASGITransport, AsyncClient

    await register_verified(other)
    ppid = _ppid((await _choose_online(other))["payment_url"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://bot.example.kz") as third:
        await register_verified(third, email="c@example.kz", slug="drugoi", phone="+7 702 000 00 00")
        assert (await third.get(f"/api/payments/mock/{ppid}")).status_code == 404
        assert (await third.post(f"/api/payments/mock/{ppid}/sign", headers=csrf(third),
                                 json={"result": "succeeded"})).status_code == 404


async def test_pay_issued_invoice_online(client, other, pool, internal, online):
    await register_verified(other)
    await other.post("/api/me/tariff", headers=csrf(other), json={"tariff": "business", "method": "invoice"})
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    number = (await client.post(f"/api/staff/applications/{aid}/issue-invoice", headers=csrf(client),
                                json={"kind": "subscription", "amount_kzt": 95000})).json()["number"]
    r = await other.post(f"/api/me/invoices/{number}/pay-online", headers=csrf(other))
    assert r.status_code == 200, r.text
    ppid = _ppid(r.json()["redirect_url"])
    assert (await _sign_via_page(other, ppid)).json()["outcome"] == "applied"
    acc = await pool.fetchrow("SELECT status, invoice_no, invoice_amount FROM accounts")
    assert (acc["status"], acc["invoice_no"], acc["invoice_amount"]) == ("approved", number, 95000)
    assert await pool.fetchval("SELECT invoice_no FROM payments") == number
    assert (await other.post(f"/api/me/invoices/{number}/pay-online", headers=csrf(other))).status_code == 409


async def test_online_disabled_errors(client, other, pool):
    aid, _ = await make_account(pool)
    await login(other)
    r = await other.post("/api/me/topup", headers=csrf(other), json={"method": "online"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "online_disabled"
    assert (await other.post("/api/me/invoices/NOX-1/pay-online", headers=csrf(other))).status_code == 409


# ---- top-up, renewal, upgrade, refund -------------------------------------------------------


async def test_online_topup_grants_coins(client, other, pool, online):
    aid, key = await make_account(pool, coins=0)
    await login(other)
    r = await other.post("/api/me/topup", headers=csrf(other), json={"method": "online"})
    assert r.status_code == 200 and r.json()["kind"] == "online"
    ppid = _ppid(r.json()["redirect_url"])
    assert (await _sign_via_page(other, ppid)).json()["outcome"] == "applied"
    bal = (await client.get("/llm/v1/balance", headers={"x-api-key": key})).json()
    assert bal["coins_left"] == 10000
    inv = await pool.fetchrow("SELECT kind, paid_at, payment_id FROM invoices")
    assert inv["kind"] == "topup" and inv["paid_at"] is not None
    assert await pool.fetchval("SELECT method FROM payments WHERE id = $1", inv["payment_id"]) == "online:mock"
    assert await pool.fetchval("SELECT payment_id FROM online_payments") == inv["payment_id"]


async def test_online_renewal_invoice(client, other, pool, online):
    aid, _ = await make_account(pool, coins=1000)
    await pool.execute("UPDATE accounts SET period_end = now() - interval '1 hour' WHERE id = $1", aid)
    await staff_login(client, pool)
    number = (await client.post(f"/api/staff/applications/{aid}/issue-invoice", headers=csrf(client),
                                json={"kind": "renewal"})).json()["number"]
    await login(other)
    ppid = _ppid((await other.post(f"/api/me/invoices/{number}/pay-online", headers=csrf(other))).json()["redirect_url"])
    old_end = await pool.fetchval("SELECT period_end FROM accounts")
    assert (await _sign_via_page(other, ppid)).json()["outcome"] == "applied"
    assert await pool.fetchval("SELECT paid_at IS NOT NULL FROM invoices WHERE number = $1", number)
    assert await pool.fetchval("SELECT period_start FROM accounts") == old_end, "rolled over"


async def test_online_upgrade_from_free(client, other, pool, internal, online):
    await register_verified(other)
    assert (await other.post("/api/me/tariff", headers=csrf(other), json={"tariff": "free"})).status_code == 200
    job = (await internal.post("/internal/jobs/claim")).json()
    await internal.post(f"/internal/jobs/{job['id']}/result",
                        json={"ok": True, "port": 8110, "admin_url": "https://primer.example.kz"})
    me = await _choose_online(other, "pro")
    assert me["status"] == "active" and me["tariff"] == "free" and "/pay/mock/" in me["payment_url"]
    assert await pool.fetchval("SELECT kind FROM invoices") == "upgrade"
    assert (await _sign_via_page(other, _ppid(me["payment_url"]))).json()["outcome"] == "applied"
    acc = await pool.fetchrow("SELECT status, tariff, coins_limit, free_until FROM accounts")
    assert (acc["status"], acc["tariff"], acc["coins_limit"], acc["free_until"]) == ("active", "pro", 120000, None)
    assert await pool.fetchval("SELECT count(*) FROM jobs WHERE status = 'pending'") == 0, "active: nothing to resume"


async def test_refund_adds_negative_ledger_row(client, other, pool, internal, online):
    await register_verified(other)
    ppid = _ppid((await _choose_online(other))["payment_url"])
    await _sign_via_page(other, ppid)
    r = await _webhook(client, ppid, "refunded", 59990)
    assert r.json()["outcome"] == "applied"
    assert sorted(await pool.fetch("SELECT amount_kzt FROM payments"), key=lambda r: r[0]) == [(-59990,), (59990,)]
    assert await pool.fetchval("SELECT status FROM online_payments") == "refunded"
    assert (await _webhook(client, ppid, "refunded", 59990)).json()["outcome"] == "duplicate"


async def test_recurring_token_only_when_flagged(client, other, pool, online, monkeypatch):
    await register_verified(other)
    ppid = _ppid((await _choose_online(other))["payment_url"])
    # a token in the payload is ignored while the provider does not support recurring
    raw = json.dumps({"event_id": "e1", "payment_id": ppid, "status": "succeeded", "amount_kzt": 59990,
                      "token": "tok_x"}).encode()
    assert (await _webhook(client, ppid, raw=raw)).json()["outcome"] == "applied"
    assert await pool.fetchval("SELECT pay_token FROM accounts") is None
    aid, _ = await make_account(pool, email="r@example.kz", slug="rec")
    monkeypatch.setattr(get_settings(), "payment_mock_recurring", True)
    await login(other, "r@example.kz")
    ppid = _ppid((await other.post("/api/me/topup", headers=csrf(other), json={"method": "online"})).json()["redirect_url"])
    await _sign_via_page(other, ppid)
    row = await pool.fetchrow("SELECT pay_token, pay_token_provider FROM accounts WHERE id = $1", aid)
    assert row["pay_token"].startswith("mocktok_") and row["pay_token_provider"] == "mock"


# ---- migration 0004 on old data -------------------------------------------------------------


async def test_migration_0004_keeps_old_accounts(tmp_path):
    name = "nox_portal_mig_0004"
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{name}"')
    old = tmp_path / "old"
    old.mkdir()
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.name < "0004":
            shutil.copy(f, old / f.name)
    pool = await asyncpg.create_pool(f"{_base}/{name}", min_size=1, max_size=2)
    try:
        assert await migrate(pool, old) == ["0001_init.sql", "0002_invoices.sql", "0003_llm_gateway.sql"]
        aid = await pool.fetchval(
            "INSERT INTO accounts(email, password_hash, company, contact_name, phone, slug, status, consent_at,"
            " tariff, invoice_no, invoice_amount, invoice_issued_at) VALUES ('old@example.kz', 'x', 'Old', 'O',"
            " '+7 700 000 00 01', 'old', 'awaiting_payment', now(), 'start', 'NOX-2026-00007', 59990, now())"
            " RETURNING id")
        await pool.execute("INSERT INTO invoices(account_id, number, kind, tariff, amount_kzt, coins, period_start)"
                           " VALUES ($1, 'NOX-2026-00008', 'renewal', 'start', 59990, 30000, now())", aid)
        assert (await migrate(pool, MIGRATIONS_DIR))[0] == "0004_payments.sql"
        acc = await pool.fetchrow("SELECT status, invoice_no, invoice_amount, pay_method, invoice_note, free_until"
                                  " FROM accounts")
        assert tuple(acc) == ("awaiting_payment", "NOX-2026-00007", 59990, None, "", None)
        inv = await pool.fetchrow("SELECT note, staff_id FROM invoices")
        assert tuple(inv) == ("", None)
        await pool.execute("UPDATE accounts SET status = 'awaiting_invoice'")
        await pool.execute("UPDATE accounts SET status = 'payment_pending'")
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute("UPDATE accounts SET status = 'awaiting_approval'")
        await pool.execute("INSERT INTO invoices(account_id, number, kind, tariff, amount_kzt, coins)"
                           " VALUES ($1, 'NOX-2026-00009', 'upgrade', 'pro', 149990, 120000)", aid)
        assert await migrate(pool, MIGRATIONS_DIR) == [], "applied once"
    finally:
        await pool.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
    assert REG  # shared fixtures imported
