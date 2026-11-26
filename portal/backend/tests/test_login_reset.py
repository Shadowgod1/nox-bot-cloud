from nox_portal import mailer
from tests.conftest import REG, csrf, last_code, register, register_verified

LOGIN = {"email": REG["email"], "password": REG["password"]}


async def test_login_before_verification(client):
    await register(client)
    r = await client.post("/api/auth/login", json=LOGIN)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "email_not_verified"
    assert not client.cookies.get("nox_session")


async def test_login_me_logout(client, other):
    await register_verified(client)
    r = await other.post("/api/auth/login", json={**LOGIN, "email": "OWNER@example.kz"})
    assert r.status_code == 200
    assert r.json()["email"] == REG["email"]
    assert "secure" in r.headers.get("set-cookie", "").lower()
    assert "httponly" in r.headers.get("set-cookie", "").lower()
    assert (await other.get("/api/me")).status_code == 200
    await other.post("/api/auth/logout")
    assert (await other.get("/api/me")).status_code == 401


async def test_wrong_password_and_unknown_email_look_the_same(client):
    await register_verified(client)
    a = await client.post("/api/auth/login", json={**LOGIN, "password": "wrong-password-x"})
    b = await client.post("/api/auth/login", json={**LOGIN, "email": "nobody@example.kz"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


async def test_lockout_after_10_failures(client, other, pool):
    await register_verified(client)
    for _ in range(10):
        r = await other.post("/api/auth/login", json={**LOGIN, "password": "wrong-password-x"})
        assert r.status_code == 401
    r = await other.post("/api/auth/login", json=LOGIN)
    assert r.status_code == 401, "locked account must refuse even the right password"
    assert await pool.fetchval("SELECT locked_until > now() FROM accounts") is True
    assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE action = 'client.locked'") == 1
    await pool.execute("UPDATE accounts SET locked_until = now() - interval '1 second'")
    assert (await other.post("/api/auth/login", json=LOGIN)).status_code == 200


async def test_login_rate_limited_per_ip(client):
    for i in range(30):
        await client.post("/api/auth/login", json={"email": f"x{i}@example.kz", "password": "whatever-123"})
    r = await client.post("/api/auth/login", json=LOGIN)
    assert r.status_code == 429


async def test_password_reset_flow(client, other):
    await register_verified(client)
    assert (await client.get("/api/me")).status_code == 200
    mailer.OUTBOX.clear()
    r = await other.post("/api/auth/reset/request", json={"email": REG["email"]})
    assert r.status_code == 200
    code = last_code(REG["email"])
    assert "Сброс пароля" in mailer.OUTBOX[-1].subject
    r = await other.post("/api/auth/reset/confirm",
                         json={"email": REG["email"], "code": code, "password": "brand-new-pass-2"})
    assert r.status_code == 200, r.text
    # the old session is gone (password fingerprint changed)
    assert (await client.get("/api/me")).status_code == 401
    assert (await other.post("/api/auth/login", json=LOGIN)).status_code == 401
    assert (await other.post("/api/auth/login", json={**LOGIN, "password": "brand-new-pass-2"})).status_code == 200


async def test_reset_unknown_email_silent(client):
    r = await client.post("/api/auth/reset/request", json={"email": "nobody@example.kz"})
    assert r.status_code == 200
    assert mailer.OUTBOX == []


async def test_reset_wrong_code_and_weak_password(client, pool):
    await register_verified(client)
    await client.post("/api/auth/reset/request", json={"email": REG["email"]})
    code = last_code(REG["email"])
    r = await client.post("/api/auth/reset/confirm",
                          json={"email": REG["email"], "code": code, "password": "short"})
    assert r.status_code == 422
    wrong = "000000" if code != "000000" else "111111"
    r = await client.post("/api/auth/reset/confirm",
                          json={"email": REG["email"], "code": wrong, "password": "brand-new-pass-2"})
    assert r.status_code == 400


async def test_reset_clears_lockout(client, other, pool):
    await register_verified(client)
    await pool.execute("UPDATE accounts SET locked_until = now() + interval '10 minutes'")
    await other.post("/api/auth/reset/request", json={"email": REG["email"]})
    await other.post("/api/auth/reset/confirm",
                     json={"email": REG["email"], "code": last_code(REG["email"]), "password": "brand-new-pass-2"})
    assert (await other.post("/api/auth/login", json={**LOGIN, "password": "brand-new-pass-2"})).status_code == 200


async def test_setup_link_requires_csrf(client):
    await register_verified(client)
    r = await client.post("/api/me/setup-link")
    assert r.status_code == 403
    r = await client.post("/api/me/setup-link", headers=csrf(client))
    assert r.status_code == 404
