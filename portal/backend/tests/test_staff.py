import re

import pytest

from nox_portal import mailer
from tests.conftest import REG, csrf, make_staff, register, register_verified, staff_login, unseal

KEY = "sk-ant-api03-" + "A" * 40


def _staff_routes(_app):
    from nox_portal.api import staff

    out = []
    for route in staff.router.routes:  # FastAPI includes routers lazily: walk the router itself
        path = getattr(route, "path", "")
        if not path.startswith("/api/staff") or path in ("/api/staff/auth/login", "/api/staff/auth/logout"):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            out.append((method, re.sub(r"\{[^}]+\}", "1", path)))
    return out


async def test_staff_routes_exist(app):
    paths = {p for _, p in _staff_routes(app)}
    assert "/api/staff/applications/1/approve" in paths
    assert len(_staff_routes(app)) >= 12


async def test_every_staff_endpoint_rejects_anonymous(app, client):
    for method, path in _staff_routes(app):
        r = await client.request(method, path, json={})
        assert r.status_code == 401, (method, path, r.status_code)


async def test_every_staff_endpoint_rejects_client_session(app, client):
    await register_verified(client)
    for method, path in _staff_routes(app):
        r = await client.request(method, path, json={}, headers=csrf(client))
        assert r.status_code == 403, (method, path, r.status_code)


async def test_client_endpoints_reject_staff_session(client, pool):
    await staff_login(client, pool)
    assert (await client.get("/api/me")).status_code == 403


async def test_staff_mutations_need_csrf(app, client, pool, other):
    await register_verified(other)
    await staff_login(client, pool)
    for method, path in _staff_routes(app):
        if method == "POST":
            r = await client.post(path, json={"coins": 30000, "reason": "xxx", "confirm_slug": "x"})
            assert r.status_code == 403, path
            assert r.json()["error"]["code"] == "csrf_failed"


async def test_staff_login_lockout_and_disabled(client, pool):
    await make_staff(pool)
    for _ in range(10):
        r = await client.post("/api/staff/auth/login", json={"email": "ops@example.kz", "password": "wrong-password"})
        assert r.status_code == 401
    r = await client.post("/api/staff/auth/login", json={"email": "ops@example.kz", "password": "staff-password-1"})
    assert r.status_code == 401
    await pool.execute("UPDATE staff SET locked_until = NULL, disabled = true")
    r = await client.post("/api/staff/auth/login", json={"email": "ops@example.kz", "password": "staff-password-1"})
    assert r.status_code == 401


async def test_disabled_staff_session_ends(client, pool):
    await staff_login(client, pool)
    assert (await client.get("/api/staff/auth/me")).status_code == 200
    await pool.execute("UPDATE staff SET disabled = true")
    assert (await client.get("/api/staff/auth/me")).status_code == 401


async def test_list_filters_and_detail(client, other, pool):
    await register_verified(other)
    await register(other, email="b@example.kz", slug="second")  # stays "new"
    await staff_login(client, pool)
    rows = (await client.get("/api/staff/applications")).json()
    assert {r["slug"] for r in rows} == {"primer", "second"}
    rows = (await client.get("/api/staff/applications", params={"status": "verified"})).json()
    assert [r["slug"] for r in rows] == ["primer"]
    rows = (await client.get("/api/staff/applications", params={"q": "SECOND"})).json()
    assert [r["slug"] for r in rows] == ["second"]
    assert (await client.get("/api/staff/applications", params={"status": "bogus"})).status_code == 422
    counts = (await client.get("/api/staff/counts")).json()
    assert counts == {"verified": 1, "new": 1}
    detail = (await client.get(f"/api/staff/applications/{rows[0]['id']}")).json()
    assert detail["bot_host"] == "second.example.kz"
    assert "password_hash" not in detail


async def _verified_account_id(other, pool) -> int:
    await register_verified(other)
    return await pool.fetchval("SELECT id FROM accounts WHERE email = $1", REG["email"])


async def test_approve_with_custom_key_seals_it_and_never_returns_it(client, other, pool):
    aid = await _verified_account_id(other, pool)
    await staff_login(client, pool)
    r = await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client),
                          json={"coins": 100000, "anthropic_key": KEY})
    assert r.status_code == 200, r.text
    job = await pool.fetchrow("SELECT kind, status, coins, slug, llm_key_sealed FROM jobs")
    assert (job["kind"], job["status"], job["coins"], job["slug"]) == ("create", "pending", 100000, "primer")
    assert KEY not in job["llm_key_sealed"]
    assert unseal(job["llm_key_sealed"]) == KEY
    assert await pool.fetchval("SELECT status FROM accounts WHERE id = $1", aid) == "approved"
    # nothing anywhere in the API shows the key or the sealed blob
    for path in (f"/api/staff/applications/{aid}", "/api/staff/applications", "/api/staff/audit"):
        text = (await client.get(path)).text
        assert KEY not in text and job["llm_key_sealed"] not in text and "sk-ant" not in text
    detail = (await client.get(f"/api/staff/applications/{aid}")).json()
    assert detail["jobs"][0]["has_custom_key"] is True
    assert "sk-ant" not in (await other.get("/api/me")).text
    audit = await pool.fetchrow("SELECT details FROM audit_log WHERE action = 'staff.approve'")
    assert audit["details"]["custom_key"] is True and audit["details"]["coins"] == 100000


async def test_approve_without_key_and_slug_change(client, other, pool):
    aid = await _verified_account_id(other, pool)
    await staff_login(client, pool)
    r = await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client),
                          json={"coins": 30000, "slug": "primer-kz"})
    assert r.status_code == 200
    assert await pool.fetchval("SELECT llm_key_sealed FROM jobs") is None
    assert await pool.fetchval("SELECT slug FROM accounts") == "primer-kz"
    assert await pool.fetchval("SELECT slug FROM jobs") == "primer-kz"
    # a second approve is refused (wrong status)
    r = await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client), json={"coins": 30000})
    assert r.status_code == 409


@pytest.mark.parametrize("body,code", [
    ({"coins": -1}, 422),
    ({"coins": 1_000_000_000}, 422),
    ({"coins": 30000, "slug": "admin"}, 422),
    ({"coins": 30000, "anthropic_key": "not-a-key"}, 422),
    ({"coins": 30000, "anthropic_key": "sk-ant-api03-has space inside-xxxxxxxx"}, 422),
    ({}, 422),  # no tariff chosen: coins are required
    ({"tariff": "gold"}, 422),
])
async def test_approve_validation(client, other, pool, body, code):
    aid = await _verified_account_id(other, pool)
    await staff_login(client, pool)
    r = await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client), json=body)
    assert r.status_code == code, r.text
    assert "sk-ant" not in r.text or "has space" not in r.text


async def test_approve_slug_taken(app, client, other, pool):
    from httpx import ASGITransport, AsyncClient

    aid = await _verified_account_id(other, pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://bot.example.kz") as third:
        await register(third, email="c@example.kz", slug="taken")
    await staff_login(client, pool)
    r = await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client),
                          json={"coins": 30000, "slug": "taken"})
    assert r.status_code == 409


async def test_reject_mails_reason_and_wipes_queued_job(client, other, pool):
    aid = await _verified_account_id(other, pool)
    await staff_login(client, pool)
    await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client),
                      json={"coins": 30000, "anthropic_key": KEY})
    mailer.OUTBOX.clear()
    r = await client.post(f"/api/staff/applications/{aid}/reject", headers=csrf(client),
                          json={"reason": "Не удалось связаться с компанией"})
    assert r.status_code == 200
    assert await pool.fetchval("SELECT status FROM accounts") == "rejected"
    job = await pool.fetchrow("SELECT status, llm_key_sealed FROM jobs")
    assert job["status"] == "failed" and job["llm_key_sealed"] is None
    assert mailer.OUTBOX[-1].to == REG["email"]
    assert "Не удалось связаться" in mailer.OUTBOX[-1].text
    me = (await other.get("/api/me")).json()
    assert me["status"] == "rejected" and me["reject_reason"] == "Не удалось связаться с компанией"


async def test_audit_log_records_staff_actions(client, other, pool):
    aid = await _verified_account_id(other, pool)
    await staff_login(client, pool)
    await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client), json={"coins": 30000})
    rows = (await client.get("/api/staff/audit")).json()
    actions = [r["action"] for r in rows]
    assert "staff.approve" in actions and "staff.login" in actions and "client.verified" in actions
    approve = next(r for r in rows if r["action"] == "staff.approve")
    assert approve["staff_email"] == "ops@example.kz"
    assert approve["company"] == REG["company"]


async def test_staff_config(client, pool):
    await staff_login(client, pool)
    cfg = (await client.get("/api/staff/config")).json()
    assert [t["code"] for t in cfg.pop("tariffs")] == ["start", "business", "pro", "free"]
    assert cfg.pop("topup") == {"code": "topup", "name": "Пакет коинов", "price_kzt": 9900, "coins": 10000,
                                "description": "10 000 коинов сверх месячного пакета, не сгорают", "available": True,
                                "dialogs": 500}
    assert cfg.pop("gateway_url") == "https://bot.example.kz"
    assert cfg == {"coin_presets": [30000, 60000, 120000], "instance_domain": "example.kz",
                   "custom_key_supported": True, "online_payments": False}
