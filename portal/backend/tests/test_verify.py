from nox_portal import mailer
from tests.conftest import REG, last_code, register


async def _verify(c, code):
    return await c.post("/api/auth/verify", json={"email": REG["email"], "code": code})


async def test_verify_success_logs_in_and_notifies_staff(client, pool):
    await register(client)
    code = last_code(REG["email"])
    r = await _verify(client, code)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "verified"
    assert body["bot_host"] == "primer.example.kz"
    assert "password_hash" not in body
    assert client.cookies.get("nox_session")
    assert client.cookies.get("nox_csrf")
    assert (await client.get("/api/me")).json()["status"] == "verified"
    assert any(m.to == "staff@example.kz" and "Новая заявка" in m.subject for m in mailer.OUTBOX)
    # the code is single use
    r = await _verify(client, code)
    assert r.status_code == 400


async def test_codes_are_stored_hashed(client, pool):
    await register(client)
    code = last_code(REG["email"])
    stored = await pool.fetchval("SELECT code_hash FROM email_codes")
    assert code not in stored
    assert len(stored) == 64


async def test_wrong_code_attempts_exhaust(client):
    await register(client)
    code = last_code(REG["email"])
    wrong = "000000" if code != "000000" else "111111"
    for left in (4, 3, 2, 1):
        r = await _verify(client, wrong)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_code"
        assert str(left) in r.json()["error"]["message"]
    r = await _verify(client, wrong)
    assert r.json()["error"]["code"] == "code_exhausted"
    # even the right code no longer works
    r = await _verify(client, code)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "code_exhausted"


async def test_expired_code(client, pool):
    await register(client)
    code = last_code(REG["email"])
    await pool.execute("UPDATE email_codes SET expires_at = now() - interval '1 second'")
    r = await _verify(client, code)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "code_expired"


async def test_code_ttl_is_15_minutes(client, pool):
    await register(client)
    minutes = await pool.fetchval("SELECT extract(epoch FROM expires_at - created_at) / 60 FROM email_codes")
    assert round(float(minutes)) == 15


async def test_resend_cooldown_and_new_code_invalidates_old(client, pool):
    await register(client)
    old = last_code(REG["email"])
    r = await client.post("/api/auth/resend", json={"email": REG["email"]})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "resend_cooldown"
    await pool.execute("UPDATE email_codes SET created_at = now() - interval '61 seconds'")
    r = await client.post("/api/auth/resend", json={"email": REG["email"]})
    assert r.status_code == 200
    new = last_code(REG["email"])
    if new != old:
        assert (await _verify(client, old)).status_code == 400
    assert (await _verify(client, new)).status_code == 200


async def test_resend_unknown_email_is_silent(client):
    r = await client.post("/api/auth/resend", json={"email": "nobody@example.kz"})
    assert r.status_code == 200
    assert mailer.OUTBOX == []


async def test_verify_unknown_email(client):
    r = await client.post("/api/auth/verify", json={"email": "nobody@example.kz", "code": "123456"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_code"


async def test_verify_code_format(client):
    r = await client.post("/api/auth/verify", json={"email": REG["email"], "code": "12ab56"})
    assert r.status_code == 422
