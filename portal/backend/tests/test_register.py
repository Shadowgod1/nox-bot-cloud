import pytest

from nox_portal import mailer, slugs
from nox_portal.config import get_settings
from tests.conftest import REG, register, register_verified


async def test_register_creates_unverified_account_and_mails_code(client, pool):
    await register(client)
    row = await pool.fetchrow("SELECT status, slug, password_hash, consent_at FROM accounts WHERE email = $1",
                              REG["email"])
    assert row["status"] == "new"
    assert row["slug"] == "primer"
    assert row["password_hash"].startswith("$argon2id$")
    assert row["consent_at"] is not None
    assert len(mailer.OUTBOX) == 1
    assert mailer.OUTBOX[0].to == REG["email"]
    assert "Код подтверждения" in mailer.OUTBOX[0].subject


async def test_register_normalizes_email_and_slug(client, pool):
    await register(client, email="  Owner@Example.KZ ", slug="Primer")
    assert await pool.fetchval("SELECT slug FROM accounts WHERE email = 'owner@example.kz'") == "primer"


@pytest.mark.parametrize("field,value", [
    ("password", "short-pw"),
    ("email", "not-an-email"),
    ("phone", "abc"),
    ("company", "   "),
    ("consent", False),
    ("slug", "ab"),
    ("slug", "1abc"),
    ("slug", "abc-"),
    ("slug", "a_bc"),
    ("slug", "a" * 33),
    ("slug", "xn--abc"),
])
async def test_register_validation(client, field, value):
    r = await client.post("/api/auth/register", json={**REG, field: value})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"


async def test_broken_json_body_gets_a_russian_message(client):
    r = await client.post("/api/auth/register", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["error"] == {"code": "validation_error", "message": "Неверный формат запроса"}


@pytest.mark.parametrize("slug", sorted(slugs.RESERVED))
async def test_reserved_slugs_rejected(client, slug):
    r = await client.post("/api/auth/register", json={**REG, "slug": slug})
    assert r.status_code == 422, (slug, r.text)
    assert "зарезервирован" in r.json()["error"]["message"] or "символа" in r.json()["error"]["message"]


async def test_extra_reserved_slugs_from_settings(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "reserved_slugs", "acme, Partner")
    r = await client.post("/api/auth/register", json={**REG, "slug": "partner"})
    assert r.status_code == 422
    r = await client.get("/api/slug-check", params={"slug": "acme"})
    assert r.json()["available"] is False


def test_slug_rules_unit():
    assert slugs.check("abc") is None
    assert slugs.check("my-shop-2") is None
    assert slugs.check("a" * 32) is None
    assert slugs.check("a" * 33) is not None
    assert slugs.check("-abc") is not None
    assert slugs.check("www") is not None


async def test_slug_taken_by_another_email(client, other):
    await register(client)
    r = await other.post("/api/auth/register", json={**REG, "email": "second@example.kz"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "slug_taken"
    r = await other.get("/api/slug-check", params={"slug": "primer"})
    assert r.json() == {"slug": "primer", "available": False, "message": "этот адрес уже занят",
                        "suggestion": "primer-2"}
    r = await other.get("/api/slug-check", params={"slug": "free-name"})
    assert r.json()["available"] is True


async def test_abandoned_unverified_registration_frees_slug(client, other, pool):
    await register(client)
    await pool.execute("UPDATE accounts SET created_at = now() - interval '25 hours'")
    await register(other, email="second@example.kz")
    assert await pool.fetchval("SELECT email FROM accounts WHERE slug = 'primer'") == "second@example.kz"


async def test_reregister_unverified_replaces_data(client, pool):
    await register(client)
    await pool.execute("UPDATE email_codes SET created_at = now() - interval '2 minutes'")
    await register(client, company="ТОО Новое", slug="novoe")
    row = await pool.fetchrow("SELECT company, slug FROM accounts WHERE email = $1", REG["email"])
    assert (row["company"], row["slug"]) == ("ТОО Новое", "novoe")
    assert await pool.fetchval("SELECT count(*) FROM accounts") == 1


async def test_register_existing_verified_email_does_not_enumerate(client, other, pool):
    await register_verified(client)
    mailer.OUTBOX.clear()
    r = await other.post("/api/auth/register", json={**REG, "slug": "another"})
    assert r.status_code == 202
    assert [m.subject for m in mailer.OUTBOX] == ["Повторная регистрация в Nox Bot"]
    assert await pool.fetchval("SELECT slug FROM accounts WHERE email = $1", REG["email"]) == "primer"


async def test_register_rate_limited_per_ip(client):
    for i in range(10):
        r = await client.post("/api/auth/register", json={**REG, "email": f"u{i}@example.kz", "slug": f"slug-{i}"})
        assert r.status_code == 202, r.text
    r = await client.post("/api/auth/register", json={**REG, "email": "u99@example.kz", "slug": "slug-99"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in r.headers


async def test_register_rate_limit_uses_real_ip_from_proxy(client):
    """X-Real-IP is honoured only from trusted peers; the ASGI test peer is 127.0.0.1 (trusted)."""
    for i in range(10):
        r = await client.post("/api/auth/register", json={**REG, "email": f"u{i}@example.kz", "slug": f"slug-{i}"},
                              headers={"X-Real-IP": "203.0.113.5"})
        assert r.status_code == 202
    r = await client.post("/api/auth/register", json={**REG, "email": "x@example.kz", "slug": "slug-x"},
                          headers={"X-Real-IP": "203.0.113.6"})
    assert r.status_code == 202


async def test_email_failure_reports_502(client, monkeypatch):
    async def boom(*_a, **_k):
        raise mailer.MailError("smtp down")

    monkeypatch.setattr(mailer, "send", boom)
    r = await client.post("/api/auth/register", json=REG)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "email_failed"
