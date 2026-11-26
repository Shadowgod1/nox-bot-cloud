from httpx import ASGITransport, AsyncClient

from nox_portal import mailer
from tests.conftest import PROVISIONER_TOKEN, REG, csrf, register_verified, staff_login, unseal

KEY = "sk-ant-api03-" + "B" * 40
SETUP = "https://primer.example.kz/setup?token=abcdef0123456789abcdef0123456789abcdef0123456789"


async def _approved(client, other, pool, **body) -> int:
    await register_verified(other)
    aid = await pool.fetchval("SELECT id FROM accounts")
    await staff_login(client, pool)
    r = await client.post(f"/api/staff/applications/{aid}/approve", headers=csrf(client),
                          json={"coins": 30000, **body})
    assert r.status_code == 200, r.text
    return aid


async def _status(pool) -> str:
    return await pool.fetchval("SELECT status FROM accounts")


async def _ok(internal, job_id, **body):
    return await internal.post(f"/internal/jobs/{job_id}/result", json={"ok": True, **body})


# ---- isolation / auth -----------------------------------------------------------------------


async def test_internal_rejects_missing_or_wrong_token(internal_app):
    paths = [("GET", "/internal/ping"), ("POST", "/internal/jobs/claim"), ("POST", "/internal/jobs/1/result")]
    for headers in ({}, {"X-Nox-Provisioner-Token": "wrong"}, {"X-Nox-Provisioner-Token": ""},
                    {"Authorization": f"Bearer {PROVISIONER_TOKEN}"}):
        async with AsyncClient(transport=ASGITransport(app=internal_app), base_url="http://127.0.0.1") as c:
            for method, path in paths:
                r = await c.request(method, path, headers=headers, json={"ok": True})
                assert r.status_code == 401, (headers, path, r.status_code)


async def test_internal_accepts_right_token(internal):
    assert (await internal.get("/internal/ping")).json() == {"ok": True}
    assert (await internal.post("/internal/jobs/claim")).status_code == 204


async def test_public_app_has_no_internal_routes(client):
    for path in ("/internal/ping", "/internal/jobs/claim"):
        r = await client.post(path, headers={"X-Nox-Provisioner-Token": PROVISIONER_TOKEN})
        assert r.status_code in (404, 405)
    from nox_portal.api import client as client_api, staff as staff_api

    for router in (client_api.router, staff_api.router):
        assert not [r for r in router.routes if r.path.startswith("/internal")]


async def test_internal_app_has_no_public_routes(internal):
    assert (await internal.post("/api/auth/login", json={})).status_code == 404


# ---- state machine --------------------------------------------------------------------------


async def test_create_success_flow(client, other, internal, pool):
    await _approved(client, other, pool, anthropic_key=KEY)
    r = await internal.post("/internal/jobs/claim")
    assert r.status_code == 200
    job = r.json()
    assert job["kind"] == "create" and job["slug"] == "primer" and job["coins"] == 30000
    assert job["public_url"] == "https://primer.example.kz"
    assert unseal(job["llm_key_sealed"]) == KEY
    assert KEY not in r.text
    assert await _status(pool) == "provisioning"
    assert (await other.get("/api/me")).json()["status"] == "provisioning"
    # nothing else to claim
    assert (await internal.post("/internal/jobs/claim")).status_code == 204

    mailer.OUTBOX.clear()
    r = await _ok(internal, job["id"], port=8110, admin_url="https://primer.example.kz", setup_url=SETUP)
    assert r.status_code == 200, r.text
    row = await pool.fetchrow("SELECT status, port, admin_url, setup_link_enc FROM accounts")
    assert (row["status"], row["port"], row["admin_url"]) == ("active", 8110, "https://primer.example.kz")
    assert row["setup_link_enc"].startswith("enc:v1:") and "abcdef" not in row["setup_link_enc"]
    assert await pool.fetchval("SELECT llm_key_sealed FROM jobs") is None, "sealed key wiped after success"
    assert await pool.fetchval("SELECT status FROM jobs") == "done"
    ready = mailer.OUTBOX[-1]
    assert ready.to == REG["email"] and ready.subject == "Ваш бот Nox Bot готов" and SETUP in ready.text

    me = (await other.get("/api/me")).json()
    assert me["status"] == "active" and me["admin_url"] == "https://primer.example.kz"
    assert me["setup_link_available"] is True
    assert "token" not in (await other.get("/api/me")).text
    r = await other.post("/api/me/setup-link", headers=csrf(other))
    assert r.json() == {"setup_url": SETUP}
    # shown exactly once
    assert (await other.post("/api/me/setup-link", headers=csrf(other))).status_code == 404
    assert await pool.fetchval("SELECT setup_link_enc FROM accounts") is None
    # a finished job cannot be reported again
    r = await _ok(internal, job["id"], port=8110, admin_url="https://primer.example.kz")
    assert r.status_code == 409


async def test_create_failure_then_retry(client, other, internal, pool):
    aid = await _approved(client, other, pool, anthropic_key=KEY)
    job = (await internal.post("/internal/jobs/claim")).json()
    r = await internal.post(f"/internal/jobs/{job['id']}/result",
                            json={"ok": False, "error": f"install failed; key={KEY} token=abc123"})
    assert r.status_code == 200
    row = await pool.fetchrow("SELECT status, last_error FROM accounts")
    assert row["status"] == "failed"
    assert KEY not in row["last_error"] and "abc123" not in row["last_error"]
    assert await pool.fetchval("SELECT llm_key_sealed FROM jobs") is not None, "kept for retry"
    detail = (await client.get(f"/api/staff/applications/{aid}")).json()
    assert detail["status"] == "failed" and "sk-ant-api03-B" not in detail["last_error"]

    r = await client.post(f"/api/staff/applications/{aid}/retry", headers=csrf(client))
    assert r.status_code == 200
    assert await _status(pool) == "approved"
    job2 = (await internal.post("/internal/jobs/claim")).json()
    assert job2["id"] != job["id"] and unseal(job2["llm_key_sealed"]) == KEY and job2["coins"] == 30000
    assert (await _ok(internal, job2["id"], port=8111, admin_url="https://primer.example.kz")).status_code == 200
    assert await _status(pool) == "active"
    assert await pool.fetchval("SELECT count(*) FROM jobs WHERE llm_key_sealed IS NOT NULL") == 1  # the failed one
    # retry is only for failed accounts
    assert (await client.post(f"/api/staff/applications/{aid}/retry", headers=csrf(client))).status_code == 409


async def test_one_open_job_per_account(client, other, internal, pool):
    aid = await _approved(client, other, pool)
    job = (await internal.post("/internal/jobs/claim")).json()
    await _ok(internal, job["id"], port=8110, admin_url="https://primer.example.kz")
    assert (await client.post(f"/api/staff/applications/{aid}/suspend", headers=csrf(client))).status_code == 200
    r = await client.post(f"/api/staff/applications/{aid}/suspend", headers=csrf(client))
    assert r.status_code == 409


async def test_claim_timeout_marks_failed(client, other, internal, pool):
    await _approved(client, other, pool)
    job = (await internal.post("/internal/jobs/claim")).json()
    await pool.execute("UPDATE jobs SET claimed_at = now() - interval '31 minutes'")
    assert (await internal.post("/internal/jobs/claim")).status_code == 204
    assert await pool.fetchval("SELECT status FROM jobs") == "failed"
    assert await _status(pool) == "failed"
    assert (await _ok(internal, job["id"], port=8110, admin_url="https://primer.example.kz")).status_code == 409


async def test_result_validation(client, other, internal, pool):
    await _approved(client, other, pool)
    job = (await internal.post("/internal/jobs/claim")).json()
    r = await _ok(internal, job["id"], port=8110, admin_url="https://evil.example.com")
    assert r.status_code == 422
    r = await _ok(internal, job["id"], port=8110, admin_url="https://primer.example.kz",
                  setup_url="https://evil.example.com/setup?token=x")
    assert r.status_code == 422
    r = await _ok(internal, job["id"], admin_url="https://primer.example.kz")
    assert r.status_code == 422
    assert await _status(pool) == "provisioning"
    assert (await _ok(internal, 999999, port=1, admin_url="x")).status_code == 404


async def test_suspend_resume_remove(client, other, internal, pool):
    aid = await _approved(client, other, pool)
    job = (await internal.post("/internal/jobs/claim")).json()
    await _ok(internal, job["id"], port=8110, admin_url="https://primer.example.kz")
    # resume is not possible while active
    assert (await client.post(f"/api/staff/applications/{aid}/resume", headers=csrf(client))).status_code == 409

    await client.post(f"/api/staff/applications/{aid}/suspend", headers=csrf(client))
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["kind"] == "suspend" and job["llm_key_sealed"] is None
    assert await _status(pool) == "active", "status changes only when the provisioner reports success"
    await _ok(internal, job["id"])
    assert await _status(pool) == "suspended"
    assert mailer.OUTBOX[-1].subject == "Бот Nox Bot приостановлен"

    await client.post(f"/api/staff/applications/{aid}/resume", headers=csrf(client))
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["kind"] == "resume"
    await internal.post(f"/internal/jobs/{job['id']}/result", json={"ok": False, "error": "docker down"})
    assert await _status(pool) == "suspended"
    assert await pool.fetchval("SELECT last_error FROM accounts") == "docker down"
    await client.post(f"/api/staff/applications/{aid}/resume", headers=csrf(client))
    job = (await internal.post("/internal/jobs/claim")).json()
    await _ok(internal, job["id"])
    assert await _status(pool) == "active"

    r = await client.post(f"/api/staff/applications/{aid}/remove", headers=csrf(client), json={"confirm_slug": "nope"})
    assert r.status_code == 400
    r = await client.post(f"/api/staff/applications/{aid}/remove", headers=csrf(client), json={"confirm_slug": "primer"})
    assert r.status_code == 200
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["kind"] == "remove"
    await _ok(internal, job["id"])
    row = await pool.fetchrow("SELECT status, admin_url, port FROM accounts")
    assert (row["status"], row["admin_url"], row["port"]) == ("removed", None, None)


async def test_jobs_claimed_in_order(client, other, internal, pool):
    await _approved(client, other, pool)
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="https://bot.example.kz") as c3:
        await register_verified(c3, email="b@example.kz", slug="second")
    bid = await pool.fetchval("SELECT id FROM accounts WHERE slug = 'second'")
    await client.post(f"/api/staff/applications/{bid}/approve", headers=csrf(client), json={"coins": 0})
    first = (await internal.post("/internal/jobs/claim")).json()
    second = (await internal.post("/internal/jobs/claim")).json()
    assert (first["slug"], second["slug"]) == ("primer", "second")
    assert second["coins"] == 0


# ---- one account for the cabinet and the panel ------------------------------------------------


async def test_create_job_carries_the_owner_only_on_the_internal_api(client, other, internal, pool):
    from argon2 import PasswordHasher

    aid = await _approved(client, other, pool)
    stored = await pool.fetchval("SELECT password_hash FROM accounts")
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["owner_email"] == REG["email"] and job["owner_name"] == REG["contact_name"]
    assert job["company"] == REG["company"]
    assert job["owner_password_hash"] == stored and stored.startswith("$argon2id$")
    # the core verifies the portal hash with the same argon2-cffi PasswordHasher: the cabinet password works
    assert PasswordHasher().verify(job["owner_password_hash"], REG["password"])

    mailer.OUTBOX.clear()
    assert (await _ok(internal, job["id"], port=8110, admin_url="https://primer.example.kz", setup_url=SETUP,
                      owner_preset=True)).status_code == 200
    row = await pool.fetchrow("SELECT status, owner_preset, setup_link_enc FROM accounts")
    assert (row["status"], row["owner_preset"], row["setup_link_enc"]) == ("active", True, None)
    ready = mailer.OUTBOX[-1]
    assert "тем же e-mail (owner@example.kz) и паролем, что в личном кабинете" in ready.text
    assert "token=" not in ready.text and "argon2" not in ready.text

    # the public API never shows the hash: neither to the client nor to the staff
    me = await other.get("/api/me")
    assert me.json()["owner_preset"] is True and me.json()["setup_link_available"] is False
    detail = await client.get(f"/api/staff/applications/{aid}")
    listing = await client.get("/api/staff/applications")
    for resp in (me, detail, listing):
        assert resp.status_code == 200
        assert "argon2" not in resp.text and stored.rsplit("$", 1)[1] not in resp.text
        assert "password_hash" not in resp.text
    assert (await other.post("/api/me/setup-link", headers=csrf(other))).status_code == 404


async def test_owner_fields_only_on_create_jobs_and_old_provisioner_keeps_setup_link(client, other, internal, pool):
    aid = await _approved(client, other, pool)
    job = (await internal.post("/internal/jobs/claim")).json()
    # an older provisioner reports no owner_preset: the setup link path as before
    assert (await _ok(internal, job["id"], port=8110, admin_url="https://primer.example.kz",
                      setup_url=SETUP)).status_code == 200
    me = (await other.get("/api/me")).json()
    assert me["owner_preset"] is False and me["setup_link_available"] is True
    assert (await client.post(f"/api/staff/applications/{aid}/suspend", headers=csrf(client))).status_code == 200
    suspend = (await internal.post("/internal/jobs/claim")).json()
    assert suspend["kind"] == "suspend"
    assert all(suspend[k] is None for k in ("owner_email", "owner_name", "company", "owner_password_hash"))


async def test_non_argon2id_hash_is_not_handed_over(client, other, internal, pool):
    await _approved(client, other, pool)
    await pool.execute("UPDATE accounts SET password_hash = 'legacy-not-argon2'")
    job = (await internal.post("/internal/jobs/claim")).json()
    assert job["owner_password_hash"] is None and job["owner_email"] is None


async def test_failed_create_alerts_staff(client, other, internal, pool, monkeypatch):
    from nox_portal import mailer
    from nox_portal.config import get_settings

    sent: list[tuple[str, str, str]] = []

    async def capture(to, subject, text):
        sent.append((to, subject, text))
        return True

    monkeypatch.setattr(mailer, "send_quiet", capture)
    monkeypatch.setattr(get_settings(), "staff_notify", "ops@example.kz, boss@example.kz")
    await _approved(client, other, pool, anthropic_key=KEY)
    job = (await internal.post("/internal/jobs/claim")).json()
    await internal.post(f"/internal/jobs/{job['id']}/result", json={"ok": False, "error": f"boom key={KEY}"})
    staff = [m for m in sent if m[0] in ("ops@example.kz", "boss@example.kz") and "не выполнено" in m[1]]
    assert len(staff) == 2 and "не выполнено «create»" in staff[0][1]
    assert KEY not in staff[0][2] and "запустите задание заново" in staff[0][2]
