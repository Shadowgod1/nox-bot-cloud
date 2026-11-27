"""Kaspi provider skeleton: QR link from the template, check / pay callbacks with sample requests (the wire format
is unverified, see payments/kaspi_protocol.py), allowlist / Basic auth, idempotency by txn_id, exact amount."""

import base64

import pytest

from nox_portal import mailer, tariffs
from nox_portal.config import get_settings
from tests.conftest import csrf, register_verified, staff_login
from tests.gw import login, make_account

URL = "/api/payments/kaspi"
KASPI_IP = {"X-Real-IP": "10.20.30.40"}
TEMPLATE = "https://kaspi.example/pay/nox?service=TEST&account={account}&amount={amount}"


@pytest.fixture
def kaspi(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "payment_provider", "kaspi")
    monkeypatch.setattr(s, "kaspi_pay_url_template", TEMPLATE)
    monkeypatch.setattr(s, "kaspi_allowed_ips", "10.20.30.0/24")
    monkeypatch.setattr(s, "kaspi_basic_auth", "")


async def _awaiting_invoice(client, other, pool, amount=None) -> tuple[int, str]:
    await register_verified(other)
    await other.post("/api/me/tariff", headers=csrf(other), json={"tariff": "start", "method": "invoice"})
    await staff_login(client, pool)
    aid = await pool.fetchval("SELECT id FROM accounts")
    body = {"kind": "subscription", **({"amount_kzt": amount} if amount else {})}
    r = await client.post(f"/api/staff/applications/{aid}/issue-invoice", headers=csrf(client), json=body)
    return aid, r.json()["number"]


def _code(number: str) -> str:
    return "".join(c for c in number if c.isdigit())


async def test_kaspi_off_without_template(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "payment_provider", "kaspi")
    assert (await client.get("/api/public-config")).json()["online_payments"] is False
    assert (await client.get(URL, params={"command": "check"}, headers=KASPI_IP)).status_code == 404


async def test_online_choice_issues_invoice_and_shows_qr(client, other, pool, kaspi):
    cfg = (await client.get("/api/public-config")).json()
    assert cfg["online_payments"] is True and cfg["online_mode"] == "qr" and cfg["online_methods"] == ["kaspi_qr"]
    await register_verified(other)
    r = await other.post("/api/me/tariff", headers=csrf(other), json={"tariff": "start", "method": "online"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["status"] == "payment_pending" and me["invoice_no"] and me["invoice_amount"] == 59990
    assert me["payment_url"] == TEMPLATE.replace("{account}", _code(me["invoice_no"])).replace("{amount}", "59990")
    assert (await other.get("/api/me/invoice")).json()["paid"] is False, "valid for a bank transfer too"
    assert (await other.get("/api/me")).json()["payment_url"] == me["payment_url"]
    assert await pool.fetchval("SELECT count(*) FROM online_payments") == 0, "the row appears on pay"


async def test_check_and_pay_subscription(client, other, pool, internal, kaspi):
    aid, number = await _awaiting_invoice(client, other, pool)
    code = _code(number)
    r = await client.get(URL, params={"command": "check", "txn_id": "1001", "account": code, "sum": "59990.00"},
                         headers=KASPI_IP)
    assert r.status_code == 200
    assert r.json() == {"txn_id": "1001", "result": 0, "comment": f"Nox Bot, счёт № {number}", "sum": "59990.00"}
    # check without a sum tells the amount
    r = await client.get(URL, params={"command": "check", "txn_id": "1002", "account": code}, headers=KASPI_IP)
    assert r.json()["result"] == 0 and r.json()["sum"] == "59990.00"
    mailer.OUTBOX.clear()
    pay = {"command": "pay", "txn_id": "1003", "account": code, "sum": "59990.00", "txn_date": "20261126143000"}
    r = await client.get(URL, params=pay, headers=KASPI_IP)
    assert r.json()["result"] == 0 and r.json()["txn_id"] == "1003", r.text
    prv = r.json()["prv_txn"]
    assert prv == str(await pool.fetchval("SELECT id FROM online_payments"))
    acc = await pool.fetchrow("SELECT status, invoice_no FROM accounts")
    assert (acc["status"], acc["invoice_no"]) == ("approved", number)
    p = await pool.fetchrow("SELECT method, amount_kzt, reference, invoice_no FROM payments")
    assert tuple(p) == ("online:kaspi", 59990, "1003", number)
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["kind"] == "create" and job["coins"] == tariffs.get("start").coins
    assert any("Онлайн-оплата" in m.subject for m in mailer.OUTBOX)
    # Kaspi repeats the same pay: same answer, nothing twice
    r = await client.get(URL, params=pay, headers=KASPI_IP)
    assert r.json()["result"] == 0 and r.json()["prv_txn"] == prv
    assert await pool.fetchval("SELECT count(*) FROM payments") == 1
    # the invoice is paid now: check / another pay say "not found / already paid"
    r = await client.get(URL, params={"command": "check", "txn_id": "1004", "account": code}, headers=KASPI_IP)
    assert r.json()["result"] == 5
    r = await client.get(URL, params={**pay, "txn_id": "1005"}, headers=KASPI_IP)
    assert r.json()["result"] == 5
    assert await pool.fetchval("SELECT count(*) FROM payments") == 1


async def test_exact_amount_only(client, other, pool, kaspi):
    _, number = await _awaiting_invoice(client, other, pool, amount=65000)
    code = _code(number)
    for s, result in (("64999.00", 241), ("65000.50", 242), ("70000", 242)):
        r = await client.get(URL, params={"command": "pay", "txn_id": f"t{result}{s[:2]}", "account": code, "sum": s},
                             headers=KASPI_IP)
        assert r.json()["result"] == result, (s, r.text)
    assert await pool.fetchval("SELECT count(*) FROM payments") == 0
    assert await pool.fetchval("SELECT count(*) FROM online_payments") == 0
    assert await pool.fetchval("SELECT status FROM accounts") == "awaiting_payment"
    r = await client.get(URL, params={"command": "pay", "txn_id": "ok1", "account": number, "sum": "65000"},
                         headers=KASPI_IP)
    assert r.json()["result"] == 0, "the full invoice number is accepted too (digits are compared)"


async def test_unknown_account_bad_format_unknown_command(client, pool, kaspi):
    r = await client.get(URL, params={"command": "check", "txn_id": "1", "account": "999999"}, headers=KASPI_IP)
    assert r.json()["result"] == 5
    r = await client.get(URL, params={"command": "check", "txn_id": "", "account": "1"}, headers=KASPI_IP)
    assert r.json()["result"] == 4
    r = await client.get(URL, params={"command": "pay", "txn_id": "2", "account": "1", "sum": "abc"}, headers=KASPI_IP)
    assert r.json()["result"] == 4
    r = await client.get(URL, params={"command": "cancel", "txn_id": "3", "account": "1"}, headers=KASPI_IP)
    assert r.json()["result"] == 300


async def test_allowlist_and_basic_auth(client, other, pool, kaspi, monkeypatch):
    _, number = await _awaiting_invoice(client, other, pool)
    params = {"command": "check", "txn_id": "9", "account": _code(number)}
    assert (await client.get(URL, params=params, headers={"X-Real-IP": "8.8.8.8"})).status_code == 403
    assert (await client.get(URL, params=params)).status_code == 403, "loopback is not Kaspi"
    assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE action = 'payment.kaspi_refused'") == 2
    monkeypatch.setattr(get_settings(), "kaspi_allowed_ips", "")
    assert (await client.get(URL, params=params)).status_code == 403, "no protection configured: refuse everything"
    monkeypatch.setattr(get_settings(), "kaspi_basic_auth", "kaspi:s3cret-test")
    assert (await client.get(URL, params=params)).status_code == 403
    bad = base64.b64encode(b"kaspi:wrong").decode()
    assert (await client.get(URL, params=params, headers={"Authorization": f"Basic {bad}"})).status_code == 403
    good = base64.b64encode(b"kaspi:s3cret-test").decode()
    r = await client.get(URL, params=params, headers={"Authorization": f"Basic {good}"})
    assert r.status_code == 200 and r.json()["result"] == 0


async def test_post_form_and_topup_invoice(client, other, pool, kaspi):
    aid, key = await make_account(pool, coins=0)
    await login(other)
    r = await other.post("/api/me/topup", headers=csrf(other), json={"method": "online"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "online" and out["invoice_number"] and "amount=9900" in out["redirect_url"]
    again = (await other.post("/api/me/topup", headers=csrf(other), json={"method": "online"})).json()
    assert again["invoice_number"] == out["invoice_number"], "the open top-up invoice is reused"
    form = f"command=pay&txn_id=5005&account={_code(out['invoice_number'])}&sum=9900.00&txn_date=20261126120000"
    r = await client.post(URL, content=form, headers={**KASPI_IP, "content-type": "application/x-www-form-urlencoded"})
    assert r.json()["result"] == 0, r.text
    bal = (await client.get("/llm/v1/balance", headers={"x-api-key": key})).json()
    assert bal["coins_left"] == 10000
    assert await pool.fetchval("SELECT paid_at IS NOT NULL FROM invoices WHERE number = $1", out["invoice_number"])


async def test_pay_online_invoice_returns_qr(client, other, pool, kaspi):
    _, number = await _awaiting_invoice(client, other, pool)
    r = await other.post(f"/api/me/invoices/{number}/pay-online", headers=csrf(other))
    assert r.status_code == 200
    assert r.json()["qr_payload"] == r.json()["redirect_url"] and f"account={_code(number)}" in r.json()["qr_payload"]
