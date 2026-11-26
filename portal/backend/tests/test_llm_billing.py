"""Gateway accounts: keys through approval / provisioning / reissue / revoke, periods, renewals, grace and
suspension, top-ups, local installs, cabinet usage, daily summary."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from nox_portal import billing, mailer, renewal, tariffs, wallet
from tests.conftest import REG, csrf, register_verified, staff_login, unseal
from tests.gw import ANTHROPIC_MSG, login, make_account, settled, upstream  # noqa: F401

MSG = {"model": "claude-sonnet-5-5", "max_tokens": 100, "messages": []}


async def _call(c, key):
    return await c.post("/llm/anthropic/v1/messages", json=MSG, headers={"x-api-key": key})


async def _paid_cloud(client, other, pool, internal, *, body=None) -> tuple[int, str, dict]:
    """register -> tariff -> staff marks paid; returns (account id, virtual key from the job, claimed job)."""
    await register_verified(other)
    assert (await other.post("/api/me/tariff", headers=csrf(other), json={"tariff": "start"})).status_code == 200
    aid = await pool.fetchval("SELECT id FROM accounts")
    await staff_login(client, pool)
    r = await client.post(f"/api/staff/applications/{aid}/issue-invoice", headers=csrf(client),
                          json={"kind": "subscription"})
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/staff/applications/{aid}/mark-paid", headers=csrf(client),
                          json={"amount_kzt": 70000, **(body or {})})
    assert r.status_code == 200, r.text
    job = (await internal.post("/internal/jobs/claim")).json()
    key = unseal(job["gateway_key_sealed"]) if job["gateway_key_sealed"] else ""
    return aid, key, job


async def _finish(internal, job_id, ok=True, **extra):
    body = {"ok": ok, **extra}
    r = await internal.post(f"/internal/jobs/{job_id}/result", json=body)
    assert r.status_code == 200, r.text


# ---- keys -----------------------------------------------------------------------------------


async def test_mark_paid_issues_key_and_starts_period(client, other, pool, internal, upstream):
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    aid, key, job = await _paid_cloud(client, other, pool, internal)
    assert key.startswith("nox_vk_") and len(key) > 40
    assert job["llm_key_sealed"] is None
    row = await pool.fetchrow("SELECT key_hash, prefix, status FROM virtual_keys")
    assert row["key_hash"] == wallet.key_hash(key) and key.startswith(row["prefix"]) and row["status"] == "active"
    assert key not in str(await pool.fetch("SELECT * FROM jobs")), "only the sealed form is stored"
    bal = (await client.get("/llm/v1/balance", headers={"x-api-key": key})).json()
    assert bal["coins_left"] == tariffs.get("start").coins and bal["status"] == "active"
    assert (await _call(client, key)).status_code == 200  # provisioning is a live status
    await _finish(internal, job["id"], port=8110, admin_url="https://primer.example.kz")
    assert await pool.fetchval("SELECT gateway_key_sealed FROM jobs") is None
    detail = (await client.get(f"/api/staff/applications/{aid}")).json()
    assert detail["keys"][0]["prefix"] == row["prefix"] and "key_hash" not in str(detail)
    assert detail["llm"]["monthly_limit"] == 30000 and detail["jobs"][0]["has_gateway_key"] is False


async def test_custom_anthropic_key_means_no_gateway(client, other, pool, internal):
    aid, key, job = await _paid_cloud(client, other, pool, internal,
                                      body={"anthropic_key": "sk-ant-api03-" + "Q" * 40})
    assert key == "" and job["gateway_key_sealed"] is None and job["llm_key_sealed"]
    assert await pool.fetchval("SELECT count(*) FROM virtual_keys") == 0
    assert await pool.fetchval("SELECT period_start FROM accounts WHERE id = $1", aid) is None


async def test_retry_reuses_sealed_gateway_key(client, other, pool, internal):
    aid, key, job = await _paid_cloud(client, other, pool, internal)
    await _finish(internal, job["id"], ok=False, error="boom")
    assert (await client.post(f"/api/staff/applications/{aid}/retry", headers=csrf(client))).status_code == 200
    again = (await internal.post("/internal/jobs/claim")).json()
    assert unseal(again["gateway_key_sealed"]) == key


async def test_reissue_cloud_key_rekey_job(client, other, pool, internal, upstream):
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    aid, old, job = await _paid_cloud(client, other, pool, internal)
    await _finish(internal, job["id"], port=8110, admin_url="https://primer.example.kz")
    r = await client.post(f"/api/staff/applications/{aid}/key/reissue", headers=csrf(client))
    assert r.status_code == 200 and r.json()["key"] is None and r.json()["job_id"]
    rekey = (await internal.post("/internal/jobs/claim")).json()
    assert rekey["kind"] == "rekey"
    new = unseal(rekey["gateway_key_sealed"])
    assert new != old and new.startswith(r.json()["prefix"])
    # both work until the instance has the new key
    assert (await _call(client, old)).status_code == 200 and (await _call(client, new)).status_code == 200
    await _finish(internal, rekey["id"])
    assert (await _call(client, old)).status_code == 401
    assert (await _call(client, new)).status_code == 200
    # a failed rekey drops the new key, the current one keeps working
    await client.post(f"/api/staff/applications/{aid}/key/reissue", headers=csrf(client))
    failed = (await internal.post("/internal/jobs/claim")).json()
    await _finish(internal, failed["id"], ok=False, error="docker down")
    assert (await _call(client, unseal(failed["gateway_key_sealed"]))).status_code == 401
    assert (await _call(client, new)).status_code == 200
    # revoke: everything stops
    assert (await client.post(f"/api/staff/applications/{aid}/key/revoke", headers=csrf(client))).status_code == 200
    r = await _call(client, new)
    assert r.status_code == 401 and r.json()["error"]["type"] == "key_revoked"
    await settled()


async def test_reject_and_remove_revoke_keys(client, other, pool, internal):
    aid, key, job = await _paid_cloud(client, other, pool, internal)
    await _finish(internal, job["id"], port=8110, admin_url="https://primer.example.kz")
    await client.post(f"/api/staff/applications/{aid}/remove", headers=csrf(client), json={"confirm_slug": "primer"})
    rm = (await internal.post("/internal/jobs/claim")).json()
    await _finish(internal, rm["id"])
    assert await pool.fetchval("SELECT status FROM virtual_keys") == "revoked"


async def test_local_install_account(client, pool, upstream):
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    await staff_login(client, pool)
    mailer.OUTBOX.clear()
    body = {"company": "ТОО Локал", "contact_name": "Ерлан", "email": "local@example.kz", "phone": "+7 701 111 11 11",
            "slug": "lokal", "tariff": "business"}
    r = await client.post("/api/staff/local-accounts", headers=csrf(client), json=body)
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    acc = await pool.fetchrow("SELECT status, local_install, coins_limit FROM accounts")
    assert (acc["status"], acc["local_install"], acc["coins_limit"]) == ("active", True, 60000)
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
    assert (await _call(client, key)).status_code == 200
    assert "/reset" in mailer.OUTBOX[0].text and mailer.OUTBOX[0].to == "local@example.kz"
    assert (await client.post("/api/staff/local-accounts", headers=csrf(client), json=body)).status_code == 409
    # reissue for a local install: the key comes back once, the old one stops at once
    aid = r.json()["account_id"]
    r2 = await client.post(f"/api/staff/applications/{aid}/key/reissue", headers=csrf(client))
    new = r2.json()["key"]
    assert new and new != key
    assert (await _call(client, key)).status_code == 401 and (await _call(client, new)).status_code == 200
    await settled()


# ---- periods --------------------------------------------------------------------------------


async def _spend(pool, aid, coins):
    await pool.execute(
        "INSERT INTO llm_usage(account_id, provider, model, coins, request_id) VALUES ($1, 'anthropic', 'x', $2, $3)",
        aid, coins, f"t{coins}{aid}{datetime.now().timestamp()}")


async def _renewal_invoice(pool, aid):
    """What staff «Выставить счёт на продление» creates (for tests that have no staff session)."""
    async with pool.acquire() as conn, conn.transaction():
        end = await conn.fetchval("SELECT period_end FROM accounts WHERE id = $1", aid)
        return await billing.insert_invoice(conn, aid, "renewal", "start", 70000, 1000, period_start=end)


async def test_rollover_on_time_expires_monthly_keeps_topups(client, pool):
    aid, key = await make_account(pool, coins=1000)
    await pool.execute(
        "INSERT INTO coin_grants(account_id, kind, coins, note) VALUES ($1, 'topup', 10000, 'test')", aid)
    await _spend(pool, aid, 1500)
    assert (await wallet.balance(pool, aid)).left == Decimal(9500)
    # period ended an hour ago
    # (period_start stays: the monthly grant is keyed by it)
    await pool.execute("UPDATE accounts SET period_end = now() - interval '1 hour' WHERE id = $1", aid)
    old_end = await pool.fetchval("SELECT period_end FROM accounts WHERE id = $1", aid)
    mailer.OUTBOX.clear()
    out = await renewal.tick()
    assert out["renewal_notices"] == 1 and out["renewed"] == 0
    assert await pool.fetchval("SELECT count(*) FROM invoices") == 0, "renewal invoices are a staff action now"
    staff_mail = next(m for m in mailer.OUTBOX if m.to == "staff@example.kz")
    assert "Пора выставить счёт на продление" in staff_mail.subject and f"/staff/applications/{aid}" in staff_mail.text
    client_mail = next(m for m in mailer.OUTBOX if m.to == "owner@example.kz")
    assert "скоро продление" in client_mail.subject and "/cabinet" in client_mail.text
    assert (await renewal.tick())["renewal_notices"] == 0, "once per period"
    await staff_login(client, pool)
    assert (await client.get(f"/api/staff/applications/{aid}")).json()["renewal_due"] is True
    mailer.OUTBOX.clear()
    r = await client.post(f"/api/staff/applications/{aid}/issue-invoice", headers=csrf(client),
                          json={"kind": "renewal", "comment": "за декабрь"})
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/staff/applications/{aid}/issue-invoice", headers=csrf(client),
                              json={"kind": "renewal"})).status_code == 409
    assert "за декабрь" in mailer.OUTBOX[0].text and mailer.OUTBOX[0].to == "owner@example.kz"
    assert (await client.get(f"/api/staff/applications/{aid}")).json()["renewal_due"] is False
    inv = await pool.fetchrow("SELECT * FROM invoices")
    assert inv["kind"] == "renewal" and inv["period_start"] == old_end and inv["amount_kzt"] == 70000
    assert inv["note"] == "за декабрь" and inv["staff_id"] is not None
    r = await client.post(f"/api/staff/invoices/{inv['id']}/mark-paid", headers=csrf(client),
                          json={"amount_kzt": 70000, "reference": "п/п 5"})
    assert r.status_code == 200, r.text
    acc = await pool.fetchrow("SELECT period_start, period_end, topup_carry FROM accounts WHERE id = $1", aid)
    assert acc["period_start"] == old_end  # continues from the old end
    assert acc["topup_carry"] == Decimal(9500)  # 1500 spent: 1000 monthly + 500 of the top-up
    bal = await wallet.balance(pool, aid)
    assert bal.left == Decimal(10500) and bal.monthly == Decimal(1000)
    assert await pool.fetchval("SELECT count(*) FROM payments WHERE invoice_no = $1", inv["number"]) == 1
    assert (await renewal.tick())["renewal_notices"] == 0, "next notice only 5 days before the new end"


async def test_unused_monthly_does_not_roll_over(pool):
    aid, _ = await make_account(pool, coins=1000)
    await _spend(pool, aid, 100)
    await pool.execute("UPDATE accounts SET period_end = now() - interval '1 minute' WHERE id = $1", aid)
    await _renewal_invoice(pool, aid)
    await pool.execute("UPDATE invoices SET paid_at = now()")
    assert await renewal.rollover_due() == 1
    bal = await wallet.balance(pool, aid)
    assert bal.left == Decimal(1000) and bal.carry == 0


async def test_grace_then_suspend_then_pay_resumes(client, pool, upstream):
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    aid, key = await make_account(pool, local=False)
    await pool.execute("UPDATE accounts SET period_end = now() - interval '2 days' WHERE id = $1", aid)
    out = await renewal.tick()
    assert out["renewal_notices"] == 1 and out["suspended"] == 0
    assert (await _call(client, key)).status_code == 200, "grace: still working"
    await pool.execute("UPDATE accounts SET period_end = period_end - interval '2 days' WHERE id = $1", aid)
    await _renewal_invoice(pool, aid)
    mailer.OUTBOX.clear()
    assert (await renewal.tick())["suspended"] == 1
    acc = await pool.fetchrow("SELECT status, suspend_reason FROM accounts")
    assert (acc["status"], acc["suspend_reason"]) == ("suspended", "unpaid")
    assert await pool.fetchval("SELECT kind FROM jobs WHERE status = 'pending'") == "suspend"
    r = await _call(client, key)
    assert r.status_code == 401 and r.json()["error"]["type"] == "key_revoked"
    assert any("приостановлен" in m.subject for m in mailer.OUTBOX)
    # the suspend job runs, then the client pays
    await pool.execute("UPDATE jobs SET status = 'done'")
    inv_id = await pool.fetchval("SELECT id FROM invoices")
    await staff_login(client, pool)
    r = await client.post(f"/api/staff/invoices/{inv_id}/mark-paid", headers=csrf(client), json={"amount_kzt": 70000})
    assert r.status_code == 200, r.text
    acc = await pool.fetchrow("SELECT period_start, suspend_reason FROM accounts")
    assert acc["suspend_reason"] is None
    assert acc["period_start"] > datetime.now(acc["period_start"].tzinfo) - timedelta(minutes=1), "late: starts now"
    assert await pool.fetchval("SELECT kind FROM jobs WHERE status = 'pending'") == "resume"
    await settled()


async def test_local_account_suspends_without_job(pool):
    aid, _ = await make_account(pool, local=True)
    await pool.execute("UPDATE accounts SET period_end = now() - interval '4 days' WHERE id = $1", aid)
    out = await renewal.tick()
    assert out["suspended"] == 1
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 0
    await _renewal_invoice(pool, aid)
    await pool.execute("UPDATE invoices SET paid_at = now()")
    assert await renewal.rollover_due() == 1
    assert await pool.fetchval("SELECT status FROM accounts") == "active"


async def test_low_balance_mails_once_per_level(pool):
    aid, _ = await make_account(pool, coins=1000)
    await _spend(pool, aid, 850)
    mailer.OUTBOX.clear()
    assert await renewal.low_balance_notices() == 1
    assert await renewal.low_balance_notices() == 0
    await _spend(pool, aid, 120)
    assert await renewal.low_balance_notices() == 1
    assert [m.subject for m in mailer.OUTBOX] == ["Nox Bot: осталось 15% коинов", "Nox Bot: осталось 3% коинов"]


# ---- top-ups and the cabinet ----------------------------------------------------------------


async def test_topup_through_manager(client, other, pool, upstream):
    aid, key = await make_account(pool, coins=0)
    await login(other)
    mailer.OUTBOX.clear()
    r = await other.post("/api/me/topup", headers=csrf(other), json={"method": "invoice"})
    assert r.status_code == 200, r.text
    assert r.json() == {"kind": "request", "invoice_number": None, "redirect_url": None}
    assert await pool.fetchval("SELECT count(*) FROM invoices") == 0
    assert [m.to for m in mailer.OUTBOX] == ["staff@example.kz"] and "пополнение" in mailer.OUTBOX[0].subject
    # pressed again: still one open request, no second notice
    assert (await other.post("/api/me/topup", headers=csrf(other))).json()["kind"] == "request"
    assert len(mailer.OUTBOX) == 1
    assert await pool.fetchval("SELECT count(*) FROM topup_requests") == 1
    await staff_login(client, pool)
    detail = (await client.get(f"/api/staff/applications/{aid}")).json()
    assert detail["topup_requested_at"] is not None
    r = await client.post(f"/api/staff/applications/{aid}/issue-invoice", headers=csrf(client), json={"kind": "topup"})
    assert r.status_code == 200, r.text
    number = r.json()["number"]
    assert r.json()["amount_kzt"] == 9900
    assert mailer.OUTBOX[-1].to == "owner@example.kz" and number in mailer.OUTBOX[-1].subject
    assert (await client.get(f"/api/staff/applications/{aid}")).json()["topup_requested_at"] is None
    # the client now sees the open invoice instead of a new request
    again = (await other.post("/api/me/topup", headers=csrf(other))).json()
    assert again == {"kind": "invoice", "invoice_number": number, "redirect_url": None}
    inv = (await other.get(f"/api/me/invoices/{number}")).json()
    assert inv["kind"] == "topup" and inv["amount_kzt"] == 9900 and inv["tariff"]["coins"] == 10000
    assert not inv["paid"]
    assert (await _call(client, key)).status_code == 402
    inv_id = await pool.fetchval("SELECT id FROM invoices")
    r = await client.post(f"/api/staff/invoices/{inv_id}/mark-paid", headers=csrf(client), json={"amount_kzt": 9900})
    assert r.status_code == 200
    assert (await client.post(f"/api/staff/invoices/{inv_id}/mark-paid", headers=csrf(client),
                              json={"amount_kzt": 9900})).status_code == 409
    bal = (await client.get("/llm/v1/balance", headers={"x-api-key": key})).json()
    assert bal["coins_left"] == 10000
    lst = (await other.get("/api/me/invoices")).json()
    assert lst[0]["paid"] is True and lst[0]["kind"] == "topup"
    assert "начислено 10 000 коинов" in mailer.OUTBOX[-1].text
    staff_view = (await client.get(f"/api/staff/applications/{aid}/invoices/{number}")).json()
    assert staff_view["paid"] is True
    # staff may issue a custom pack without a request
    r = await client.post(f"/api/staff/applications/{aid}/issue-invoice", headers=csrf(client),
                          json={"kind": "topup", "amount_kzt": 45000, "coins": 50000})
    assert r.status_code == 200 and r.json()["amount_kzt"] == 45000
    assert await pool.fetchval("SELECT coins FROM invoices WHERE number = $1", r.json()["number"]) == 50000


async def test_topup_needs_gateway(other, pool):
    await register_verified(other)
    r = await other.post("/api/me/topup", headers=csrf(other))
    assert r.status_code == 409


async def test_cabinet_usage_by_day_and_model(client, other, pool, upstream):
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    aid, key = await make_account(pool)
    for _ in range(2):
        await _call(client, key)
    await settled()
    await login(other)
    me = (await other.get("/api/me")).json()
    assert me["llm_enabled"] is True and me["local_install"] is True
    u = (await other.get("/api/me/usage")).json()
    assert u["balance"]["spent"] == 25.6 and u["balance"]["coins_left"] == 974
    today = datetime.now(ZoneInfo("Asia/Almaty")).date().isoformat()
    assert u["by_day"] == [{"day": today, "coins": 25.6}]
    assert u["by_model"] == [{"provider": "anthropic", "model": "claude-sonnet-5-5", "requests": 2,
                              "input_tokens": 14000, "output_tokens": 1000, "coins": 25.6}]


async def test_usage_requires_gateway(other, pool):
    await register_verified(other, email=REG["email"])
    assert (await other.get("/api/me/usage")).status_code == 404


async def test_daily_summary(client, pool, upstream):
    upstream.on("POST", "anthropic.test/v1/messages", lambda r: ANTHROPIC_MSG)
    _, key = await make_account(pool)
    await _call(client, key)
    await settled()
    mailer.OUTBOX.clear()
    text = await renewal.daily_summary(datetime.now(ZoneInfo("Asia/Almaty")).date())
    assert "anthropic: 12.8 коинов ($0.01)" in text and "запросов 1" in text
    assert mailer.OUTBOX[0].to == "staff@example.kz"
    empty = await renewal.daily_summary(date(2020, 1, 1))
    assert "запросов не было" in empty
