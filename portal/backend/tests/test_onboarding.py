"""Onboarding: coins as dialogs, support contacts, "помогите с настройкой", Free trial reminders, slug suggestions,
public documents config, migration 0005."""

import shutil
from datetime import timedelta

import asyncpg
import pytest

from nox_portal import mailer, renewal, tariffs
from nox_portal.config import get_settings
from nox_portal.db import MIGRATIONS_DIR, migrate
from tests.conftest import ADMIN_DSN, REG, _base, csrf, register_verified, staff_login


async def _choose(c, code="free", method="invoice"):
    return await c.post("/api/me/tariff", headers=csrf(c), json={"tariff": code, "method": method})


async def _free_active(other, pool, internal) -> int:
    await register_verified(other)
    assert (await _choose(other, "free")).status_code == 200
    job = (await internal.post("/internal/jobs/claim")).json()
    r = await internal.post(f"/internal/jobs/{job['id']}/result",
                            json={"ok": True, "port": 8110, "admin_url": "https://primer.example.kz"})
    assert r.status_code == 200, r.text
    return await pool.fetchval("SELECT id FROM accounts")


# ---- coins -> dialogs -----------------------------------------------------------------------


def test_dialogs_rounding(monkeypatch):
    monkeypatch.setattr(get_settings(), "coins_per_dialog", 37)
    assert tariffs.dialogs(2000) == 50
    assert tariffs.dialogs(30000) == 800
    assert tariffs.dialogs(60000) == 1600
    assert tariffs.dialogs(120000) == 3200
    assert tariffs.dialogs(0) == 0 and tariffs.dialogs(-5) == 0
    assert tariffs.dialogs(37 * 7) == 7
    monkeypatch.setattr(get_settings(), "coins_per_dialog", 0)
    assert tariffs.dialogs(100) == 100, "zero is treated as 1 coin per dialog"


async def test_tariffs_and_public_config_carry_dialogs(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "coins_per_dialog", 40)
    out = (await client.get("/api/tariffs")).json()
    assert [t["dialogs"] for t in out] == [750, 1500, 3000, 50]
    cfg = (await client.get("/api/public-config")).json()
    assert cfg["coins_per_dialog"] == 40


async def test_balance_shows_dialogs_left(client, other, pool, internal):
    await _free_active(other, pool, internal)
    usage = (await other.get("/api/me/usage")).json()
    assert usage["balance"]["coins_left"] == 2000
    assert usage["balance"]["dialogs_left"] == 100  # 2 000 coins at the default 20 per dialog


# ---- support contacts -----------------------------------------------------------------------


async def test_support_contacts_in_config_and_letters(client, monkeypatch):
    cfg = (await client.get("/api/public-config")).json()
    assert cfg["support"]["phone"] == "" and cfg["support"]["whatsapp_url"] == ""
    mailer.OUTBOX.clear()
    await client.post("/api/auth/register", json=REG)
    assert "Телефон:" not in mailer._build(mailer.OUTBOX[-1]).get_body(("plain",)).get_content()

    monkeypatch.setattr(get_settings(), "support_phone", " +7 700 123 45 67 ")
    monkeypatch.setattr(get_settings(), "support_whatsapp", "8 (700) 123-45-67")
    cfg = (await client.get("/api/public-config")).json()
    assert cfg["support"] == {"phone": "+7 700 123 45 67", "whatsapp": "77001234567",
                              "whatsapp_url": "https://wa.me/77001234567", "email": "info@noxsw.kz"}
    body = mailer._build(mailer.OUTBOX[-1]).get_body(("plain",)).get_content()
    assert "Телефон: +7 700 123 45 67" in body and "WhatsApp: https://wa.me/77001234567" in body


async def test_public_config_legal_and_seller(client, monkeypatch):
    s = get_settings()
    cfg = (await client.get("/api/public-config")).json()
    assert cfg["legal_date"] == ""
    # .env.example placeholders do not reach the documents
    for k, v in {"seller_name": "Nox service", "seller_bin": "000000000000", "seller_iik": "KZ000000000000000000",
                 "seller_bik": "XXXXKZKA", "seller_address": "г. Усть-Каменогорск, ул. Примерная, 1",
                 "seller_phone": "+7 700 000 00 00"}.items():
        monkeypatch.setattr(s, k, v)
    cfg = (await client.get("/api/public-config")).json()
    assert all(v == "" for k, v in cfg["seller"].items() if k != "kbe")
    monkeypatch.setattr(s, "legal_date", "01.12.2026")
    monkeypatch.setattr(s, "seller_name", "ТОО «Нокс»")
    monkeypatch.setattr(s, "seller_bin", "123456789012")
    cfg = (await client.get("/api/public-config")).json()
    assert cfg["legal_date"] == "01.12.2026"
    assert (cfg["seller"]["name"], cfg["seller"]["bin"]) == ("ТОО «Нокс»", "123456789012")


# ---- slug suggestion ------------------------------------------------------------------------


async def test_slug_check_suggests_free_variant(client, other, pool):
    await register_verified(other)  # takes "primer"
    r = (await client.get("/api/slug-check", params={"slug": "primer"})).json()
    assert r["available"] is False and r["suggestion"] == "primer-2"
    await pool.execute("UPDATE accounts SET slug = 'primer-2'")
    r = (await client.get("/api/slug-check", params={"slug": "primer-2"})).json()
    assert r["suggestion"] == "primer-2-2"
    r = (await client.get("/api/slug-check", params={"slug": "admin"})).json()
    assert r["available"] is False and r["suggestion"] == "admin-2"
    r = (await client.get("/api/slug-check", params={"slug": "1x"})).json()
    assert r["suggestion"] is None
    r = (await client.get("/api/slug-check", params={"slug": "salon-ayana"})).json()
    assert r["available"] is True and r["suggestion"] is None


# ---- "помогите с настройкой" ----------------------------------------------------------------


async def test_setup_help_request_once_then_staff_marks_contacted(client, other, pool, internal):
    aid = await _free_active(other, pool, internal)
    mailer.OUTBOX.clear()
    r = await other.post("/api/me/setup-help", headers=csrf(other))
    assert r.status_code == 200, r.text
    first = r.json()["setup_help_requested_at"]
    assert first and r.json()["setup_help_contacted_at"] is None
    staff_mail = [m for m in mailer.OUTBOX if m.to == "staff@example.kz"]
    assert len(staff_mail) == 1 and "помочь с настройкой" in staff_mail[0].subject
    assert f"/staff/applications/{aid}" in staff_mail[0].text and REG["phone"] in staff_mail[0].text
    # a second press: same request, no new letter
    r = await other.post("/api/me/setup-help", headers=csrf(other))
    assert r.json()["setup_help_requested_at"] == first
    assert len([m for m in mailer.OUTBOX if m.to == "staff@example.kz"]) == 1
    assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE action = 'client.setup_help_requested'") == 1

    await staff_login(client, pool)
    detail = (await client.get(f"/api/staff/applications/{aid}")).json()
    assert detail["setup_help_requested_at"] == first and detail["setup_help_contacted_at"] is None
    r = await client.post(f"/api/staff/applications/{aid}/setup-help/contacted", headers=csrf(client))
    assert r.status_code == 200, r.text
    detail = (await client.get(f"/api/staff/applications/{aid}")).json()
    assert detail["setup_help_contacted_at"] is not None
    assert any(a["action"] == "staff.setup_help_contacted" for a in detail["audit"])
    r = await client.post(f"/api/staff/applications/{aid}/setup-help/contacted", headers=csrf(client))
    assert r.status_code == 409
    me = (await other.get("/api/me")).json()
    assert me["setup_help_contacted_at"] is not None
    # after the manager got in touch a new press opens a new request
    mailer.OUTBOX.clear()
    r = await other.post("/api/me/setup-help", headers=csrf(other))
    assert r.json()["setup_help_contacted_at"] is None
    assert len([m for m in mailer.OUTBOX if m.to == "staff@example.kz"]) == 1


async def test_setup_help_needs_a_bot_and_csrf(client, other, pool):
    await register_verified(other)
    assert (await other.post("/api/me/setup-help")).status_code == 403
    r = await other.post("/api/me/setup-help", headers=csrf(other))
    assert r.status_code == 409 and r.json()["error"]["code"] == "wrong_status"
    assert (await client.post("/api/me/setup-help")).status_code == 401


# ---- Free trial reminders -------------------------------------------------------------------


async def test_trial_reminders_three_days_then_one_day(client, other, pool, internal):
    aid = await _free_active(other, pool, internal)
    mailer.OUTBOX.clear()
    assert (await renewal.tick())["trial_reminders"] == 0
    await pool.execute("UPDATE accounts SET free_until = now() + interval '2 days 20 hours' WHERE id = $1", aid)
    assert (await renewal.tick())["trial_reminders"] == 1
    mail = [m for m in mailer.OUTBOX if m.to == REG["email"]]
    assert len(mail) == 1 and "тест закончится" in mail[0].subject
    assert "Осталось 3 дня" in mail[0].text and "/cabinet" in mail[0].text
    assert "переписка сохранятся" in mail[0].text
    assert (await renewal.tick())["trial_reminders"] == 0, "the 3-day reminder goes once"
    await pool.execute("UPDATE accounts SET free_until = now() + interval '20 hours' WHERE id = $1", aid)
    assert (await renewal.tick())["trial_reminders"] == 1
    assert "последние сутки" in [m for m in mailer.OUTBOX if m.to == REG["email"]][-1].text
    assert (await renewal.tick())["trial_reminders"] == 0
    assert await pool.fetchval("SELECT trial_notice_days FROM accounts") == 1
    assert await pool.fetchval("SELECT count(*) FROM audit_log WHERE action = 'billing.trial_reminder'") == 2


async def test_trial_reminder_short_trial_gets_only_the_nearest(client, other, pool, internal):
    aid = await _free_active(other, pool, internal)
    await pool.execute("UPDATE accounts SET free_until = now() + interval '10 hours' WHERE id = $1", aid)
    mailer.OUTBOX.clear()
    assert (await renewal.tick())["trial_reminders"] == 1
    assert len([m for m in mailer.OUTBOX if m.to == REG["email"]]) == 1
    # paid tariffs and ended trials get none
    await pool.execute("UPDATE accounts SET trial_notice_days = NULL, free_until = now() - interval '1 hour'")
    out = await renewal.tick()
    assert out["trial_reminders"] == 0 and out["trials_ended"] == 1


# ---- migration 0005 -------------------------------------------------------------------------


async def test_migration_0005_is_additive(tmp_path):
    name = "nox_portal_mig_0005"
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{name}"')
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.name < "0005":
            shutil.copy(f, old / f.name)
        if f.name < "0006":
            shutil.copy(f, new / f.name)
    pool = await asyncpg.create_pool(f"{_base}/{name}", min_size=1, max_size=2)
    try:
        await migrate(pool, old)
        await pool.execute(
            "INSERT INTO accounts(email, password_hash, company, contact_name, phone, slug, status, consent_at,"
            " tariff, free_until) VALUES ('old@example.kz', 'x', 'Old', 'O', '+7 700 000 00 01', 'old', 'active',"
            " now(), 'free', now() + interval '1 day')")
        assert await migrate(pool, new) == ["0005_onboarding.sql"]
        acc = await pool.fetchrow("SELECT status, tariff, setup_help_requested_at, setup_help_contacted_at,"
                                  " setup_help_contacted_by, trial_notice_days FROM accounts")
        assert tuple(acc) == ("active", "free", None, None, None, None)
        assert await migrate(pool, new) == [], "applied once"
        # 0006: existing bots keep the setup link path (owner_preset false)
        assert await migrate(pool, MIGRATIONS_DIR) == ["0006_owner_preset.sql"]
        assert await pool.fetchval("SELECT owner_preset FROM accounts") is False
        assert await migrate(pool, MIGRATIONS_DIR) == [], "applied once"
    finally:
        await pool.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.mark.parametrize("days", [3, 1])
def test_trial_letter_text(days):
    from datetime import datetime, timezone

    from nox_portal import letters

    subject, text = letters.trial_ending("Айгерим", datetime(2026, 12, 10, 7, 0, tzinfo=timezone.utc) + timedelta(), days)
    assert "10.12.2026" in subject
    assert "12:00" in text, "local time (Asia/Almaty)"
    assert "бот перестанет отвечать" in text
