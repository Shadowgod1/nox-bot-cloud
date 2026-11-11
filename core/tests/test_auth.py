"""Auth, setup wizard, users, CSRF, lockout (Task 2)."""

import time

from httpx import ASGITransport, AsyncClient

from iler.auth import tokens

OWNER = {"email": "Owner@Club.kz", "password": "owner-pass-123", "name": "Owner"}


def csrf(c: AsyncClient) -> dict:
    return {"X-ILER-CSRF": c.cookies.get("iler_csrf", "")}


async def setup_owner(c: AsyncClient) -> dict:
    r = await c.post("/api/setup/owner", json=OWNER)
    assert r.status_code == 200, r.text
    return r.json()


def _set_cookie(r, name: str) -> str | None:
    for h in r.headers.get_list("set-cookie"):
        if h.startswith(name + "="):
            return h
    return None


async def test_setup_state_true_when_no_users(client):
    r = await client.get("/api/setup/state")
    assert r.status_code == 200
    assert r.json() == {"needs_setup": True}
    await setup_owner(client)
    assert (await client.get("/api/setup/state")).json() == {"needs_setup": False}


async def test_setup_owner_only_once(client):
    user = await setup_owner(client)
    assert user["role"] == "owner" and user["email"] == "owner@club.kz"
    r = await client.post("/api/setup/owner", json={**OWNER, "email": "other@club.kz"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_set_up"


async def test_setup_owner_rejects_short_password(client):
    r = await client.post("/api/setup/owner", json={**OWNER, "password": "short"})
    assert r.status_code == 422
    assert (await client.get("/api/setup/state")).json() == {"needs_setup": True}


async def test_login_sets_httponly_strict_cookie(client):
    await setup_owner(client)
    client.cookies.clear()
    r = await client.post("/api/auth/login", json={"email": "owner@club.kz", "password": OWNER["password"]})
    assert r.status_code == 200, r.text
    session = _set_cookie(r, "iler_session")
    assert session is not None
    low = session.lower()
    assert "httponly" in low and "samesite=strict" in low
    csrf_cookie = _set_cookie(r, "iler_csrf")
    assert csrf_cookie is not None and "httponly" not in csrf_cookie.lower()
    me = await client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "owner@club.kz"


async def test_login_wrong_password_401_and_me_requires_auth(client):
    await setup_owner(client)
    client.cookies.clear()
    r = await client.post("/api/auth/login", json={"email": "owner@club.kz", "password": "wrong-password"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_lockout_after_10_failures(client, pool):
    await setup_owner(client)
    client.cookies.clear()
    for _ in range(10):
        r = await client.post("/api/auth/login", json={"email": "owner@club.kz", "password": "bad-password-x"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"email": "owner@club.kz", "password": OWNER["password"]})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "locked"
    # lock lasts ~15 minutes
    secs = await pool.fetchval("SELECT extract(epoch FROM locked_until - now()) FROM users")
    assert 14 * 60 < secs <= 15 * 60
    # after the lock expires, the correct password works again
    await pool.execute("UPDATE users SET locked_until = now() - interval '1 second'")
    r = await client.post("/api/auth/login", json={"email": "owner@club.kz", "password": OWNER["password"]})
    assert r.status_code == 200


async def test_mutation_without_csrf_403(client):
    await setup_owner(client)
    r = await client.put("/api/settings/bot", json={"name": "Бот"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"
    r = await client.put("/api/settings/bot", json={"name": "Бот"}, headers={"X-ILER-CSRF": "wrong"})
    assert r.status_code == 403
    r = await client.put("/api/settings/bot", json={"name": "Бот"}, headers=csrf(client))
    assert r.status_code == 200, r.text


async def test_logout_clears_session(client):
    await setup_owner(client)
    r = await client.post("/api/auth/logout")
    assert r.status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_operator_cannot_manage_users(client, app):
    await setup_owner(client)
    r = await client.post(
        "/api/users",
        json={"email": "op@club.kz", "password": "operator-pass-1", "name": "Op", "role": "operator"},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    op_id = r.json()["id"]
    dup = await client.post(
        "/api/users",
        json={"email": "OP@club.kz", "password": "operator-pass-1", "name": "Op", "role": "operator"},
        headers=csrf(client),
    )
    assert dup.status_code == 409
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as op:
        r = await op.post("/api/auth/login", json={"email": "op@club.kz", "password": "operator-pass-1"})
        assert r.status_code == 200
        assert (await op.get("/api/users")).status_code == 403
        r = await op.post(
            "/api/users",
            json={"email": "x@club.kz", "password": "operator-pass-1", "name": "X", "role": "owner"},
            headers=csrf(op),
        )
        assert r.status_code == 403
        assert (await op.delete(f"/api/users/{op_id}", headers=csrf(op))).status_code == 403
    users = (await client.get("/api/users")).json()
    assert {u["email"] for u in users} == {"owner@club.kz", "op@club.kz"}
    assert all("password_hash" not in u for u in users)
    assert (await client.delete(f"/api/users/{op_id}", headers=csrf(client))).status_code == 200
    assert len((await client.get("/api/users")).json()) == 1


async def test_owner_cannot_delete_self(client):
    me = await setup_owner(client)
    r = await client.delete(f"/api/users/{me['id']}", headers=csrf(client))
    assert r.status_code == 409


async def test_password_change_invalidates_old_session(client, app):
    await setup_owner(client)
    old_session = client.cookies.get("iler_session")
    r = await client.post("/api/auth/password", json={"old": "nope-nope-nope", "new": "new-pass-12345"}, headers=csrf(client))
    assert r.status_code == 401
    r = await client.post("/api/auth/password", json={"old": OWNER["password"], "new": "new-pass-12345"}, headers=csrf(client))
    assert r.status_code == 200, r.text
    assert (await client.get("/api/auth/me")).status_code == 200  # new cookie issued
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as stale:
        stale.cookies.set("iler_session", old_session)
        assert (await stale.get("/api/auth/me")).status_code == 401
    client.cookies.clear()
    r = await client.post("/api/auth/login", json={"email": "owner@club.kz", "password": "new-pass-12345"})
    assert r.status_code == 200


async def test_session_slides_when_close_to_expiry(client, pool):
    me = await setup_owner(client)
    pw_hash = await pool.fetchval("SELECT password_hash FROM users WHERE id=$1", me["id"])
    old = tokens.create_session(me["id"], pw_hash, now=time.time() - 11 * 3600)
    client.cookies.clear()
    client.cookies.set("iler_session", old)
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert _set_cookie(r, "iler_session") is not None
    fresh = await client.get("/api/auth/me")
    assert _set_cookie(fresh, "iler_session") is None  # far from expiry: no refresh


async def test_expired_or_forged_token_rejected(client, pool):
    me = await setup_owner(client)
    pw_hash = await pool.fetchval("SELECT password_hash FROM users WHERE id=$1", me["id"])
    client.cookies.clear()
    client.cookies.set("iler_session", tokens.create_session(me["id"], pw_hash, now=time.time() - 13 * 3600))
    assert (await client.get("/api/auth/me")).status_code == 401
    client.cookies.set("iler_session", "not.a.jwt")
    assert (await client.get("/api/auth/me")).status_code == 401
