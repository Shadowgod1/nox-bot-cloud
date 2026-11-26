"""Owner from the Nox portal: created at start from ILER_OWNER_* with the portal's argon2id hash (one account for
the cabinet and the panel), never overwrites an existing owner, never shows the hash, the wizard skips "owner"."""

import pytest
from argon2 import PasswordHasher

from iler import settings_store
from iler.auth import preset_owner
from iler.config import get_settings
from iler.settings_models import BotSettings

PASSWORD = "cabinet-pass-123"
# the portal hashes with argon2-cffi's PasswordHasher() defaults, exactly like nox_portal.security
PORTAL_HASH = PasswordHasher().hash(PASSWORD)
SETUP = {"X-ILER-Setup-Token": "test-setup-token"}


def csrf(c) -> dict:
    return {"X-ILER-CSRF": c.cookies.get("iler_csrf", "")}


@pytest.fixture
def portal_env(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "owner_email", "  Owner@Salon.KZ ")
    monkeypatch.setattr(s, "owner_name", "Айгерим")
    monkeypatch.setattr(s, "company", "Салон Aru")
    monkeypatch.setattr(s, "owner_password_hash", PORTAL_HASH)
    return s


async def test_owner_created_from_portal_and_cabinet_password_works(client, pool, portal_env):
    assert await preset_owner.ensure_preset_owner(pool) is True
    row = await pool.fetchrow("SELECT email, name, role, password_hash FROM users")
    assert (row["email"], row["name"], row["role"]) == ("owner@salon.kz", "Айгерим", "owner")
    assert row["password_hash"] == PORTAL_HASH
    audit = await pool.fetchrow("SELECT action, details FROM audit_log WHERE action = 'setup.owner_preset'")
    assert audit["details"] == {"email": "owner@salon.kz", "source": "portal"}

    # the wizard has no "owner" step: no setup needed, but the rest of the wizard is pending
    assert (await client.get("/api/setup/state")).json() == {"needs_setup": False, "wizard_pending": True}
    r = await client.post("/api/setup/owner", json={"email": "x@y.kz", "password": "other-pass-123"}, headers=SETUP)
    assert r.status_code == 409

    # the same e-mail and password as in the cabinet
    bad = await client.post("/api/auth/login", json={"email": "owner@salon.kz", "password": "wrong-pass-123"})
    assert bad.status_code == 401
    r = await client.post("/api/auth/login", json={"email": "OWNER@salon.kz ", "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "owner" and r.json()["email"] == "owner@salon.kz"

    # the company is already in the template field "Название"
    bot = await settings_store.get("bot", BotSettings)
    assert bot.template_values == {"business_name": "Салон Aru"}


async def test_finish_wizard(client, pool, portal_env):
    await preset_owner.ensure_preset_owner(pool)
    assert (await client.post("/api/setup/finish", headers=csrf(client))).status_code in (401, 403)  # no session
    await client.post("/api/auth/login", json={"email": "owner@salon.kz", "password": PASSWORD})
    assert (await client.post("/api/setup/finish")).status_code == 403  # CSRF
    r = await client.post("/api/setup/finish", headers=csrf(client))
    assert r.status_code == 200 and r.json() == {"needs_setup": False, "wizard_pending": False}
    assert (await client.get("/api/setup/state")).json()["wizard_pending"] is False
    assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE action = 'setup.wizard_finished'") == 1
    # a restart with the same environment changes nothing
    assert await preset_owner.ensure_preset_owner(pool) is False
    assert (await client.get("/api/setup/state")).json()["wizard_pending"] is False


async def test_existing_owner_is_never_overwritten(client, pool, portal_env):
    r = await client.post("/api/setup/owner", headers=SETUP,
                          json={"email": "first@club.kz", "password": "first-pass-123", "name": "First"})
    assert r.status_code == 200
    before = await pool.fetchrow("SELECT id, email, name, password_hash FROM users")
    await settings_store.put("bot", BotSettings(template_values={"business_name": "Своё"}))

    assert await preset_owner.ensure_preset_owner(pool) is False
    rows = await pool.fetch("SELECT id, email, name, password_hash FROM users")
    assert [dict(r) for r in rows] == [dict(before)]
    assert (await settings_store.get("bot", BotSettings)).template_values == {"business_name": "Своё"}
    assert (await client.get("/api/setup/state")).json() == {"needs_setup": False, "wizard_pending": False}
    login = await client.post("/api/auth/login", json={"email": "owner@salon.kz", "password": PASSWORD})
    assert login.status_code == 401


async def test_company_does_not_replace_a_typed_name(pool, portal_env):
    await settings_store.put("bot", BotSettings(template_values={"business_name": "Typed", "address": "Абая 1"}))
    assert await preset_owner.ensure_preset_owner(pool) is True
    values = (await settings_store.get("bot", BotSettings)).template_values
    assert values == {"business_name": "Typed", "address": "Абая 1"}


@pytest.mark.parametrize("email,password_hash", [
    ("not-an-email", PORTAL_HASH),
    ("owner@salon.kz", "plain-password-123"),
    ("owner@salon.kz", "$argon2i$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2g"),
    ("owner@salon.kz", ""),
    ("", PORTAL_HASH),
])
async def test_unusable_values_leave_the_setup_link_path(client, pool, monkeypatch, email, password_hash):
    s = get_settings()
    monkeypatch.setattr(s, "owner_email", email)
    monkeypatch.setattr(s, "owner_password_hash", password_hash)
    assert await preset_owner.ensure_preset_owner(pool) is False
    assert await pool.fetchval("SELECT count(*) FROM users") == 0
    assert (await client.get("/api/setup/state")).json() == {"needs_setup": True, "wizard_pending": False}


async def test_no_portal_data_is_a_no_op(pool):
    assert get_settings().owner_email == "" and get_settings().owner_password_hash == ""
    assert await preset_owner.ensure_preset_owner(pool) is False
    assert await pool.fetchval("SELECT count(*) FROM users") == 0


async def test_hash_never_leaks(client, pool, portal_env, caplog):
    caplog.set_level("DEBUG")
    await preset_owner.ensure_preset_owner(pool)
    secret_part = PORTAL_HASH.rsplit("$", 1)[1]
    assert secret_part not in caplog.text and "argon2" not in caplog.text
    responses = [await client.get("/api/setup/state")]
    responses.append(await client.post("/api/auth/login", json={"email": "owner@salon.kz", "password": PASSWORD}))
    for path in ("/api/auth/me", "/api/users", "/api/settings/bot", "/api/system/version", "/api/dashboard"):
        responses.append(await client.get(path))
    for r in responses:
        assert r.status_code == 200, (r.request.url, r.text)
        assert secret_part not in r.text and "argon2" not in r.text and "password" not in r.text.lower()


async def test_concurrent_manual_setup_and_preset_make_one_owner(client, pool, portal_env):
    import asyncio

    results = await asyncio.gather(
        preset_owner.ensure_preset_owner(pool),
        client.post("/api/setup/owner", headers=SETUP,
                    json={"email": "manual@club.kz", "password": "manual-pass-123", "name": "M"}),
    )
    assert await pool.fetchval("SELECT count(*) FROM users") == 1
    created_by_preset, manual = results
    assert created_by_preset != (manual.status_code == 200)
