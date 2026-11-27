import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from iler import crypto, settings_store
from iler.db import migrate
from iler.settings_models import LlmSettings, NotifySettings
from iler.tools import registry

SPEC_TABLES = {
    "users", "settings", "wa_instance", "conversations", "messages", "inbound_events",
    "outbound_intents", "booking_requests", "ai_requests", "tool_calls", "audit_log",
}
ROUTES = json.loads((Path(__file__).parent / "route_list.json").read_text(encoding="utf-8"))


async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_migrations_idempotent(pool):
    assert await migrate.run(pool) == []
    rows = await pool.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    assert SPEC_TABLES <= {r["tablename"] for r in rows}


def test_crypto_roundtrip():
    token = crypto.encrypt("секрет")
    assert token != "секрет" and token.startswith("enc:v1:")
    assert crypto.decrypt(token) == "секрет"


async def test_settings_secret_encrypted_at_rest(pool):
    await settings_store.put("notify", NotifySettings(telegram_bot_token=SecretStr("sk-test-123")))
    raw = await pool.fetchval("SELECT value::text FROM settings WHERE key='notify'")
    assert "sk-test-123" not in raw
    loaded = await settings_store.get("notify", NotifySettings)
    assert loaded.telegram_bot_token.get_secret_value() == "sk-test-123"


async def test_legacy_settings_rows_are_ignored(pool):
    """Older installs may still hold a row for a removed section (the dropped workstation integration
    stored one under "senet"); loading settings must not trip over it."""
    payload = {"enabled": True, "orgname": "club", "app_key": {settings_store.ENC: crypto.encrypt("x" * 12)}}
    await pool.execute("INSERT INTO settings(key, value) VALUES ('senet', $1)", payload)
    loaded = await settings_store.load_all()
    assert not hasattr(loaded, "senet")
    assert loaded.llm == LlmSettings()


async def test_settings_default_when_missing(pool):
    loaded = await settings_store.get("llm", LlmSettings)
    assert loaded.provider == "anthropic" and loaded.anthropic_model == "claude-haiku-5-5"


@pytest.mark.parametrize("method,path", ROUTES)
async def test_stub_routes_exist(client, method, path):
    url = path.replace("{", "").replace("}", "")  # fill params with their names
    r = await client.request(method.upper(), url)
    assert r.status_code != 404 and r.status_code != 405, f"{method} {path} missing"


def test_registry_loads_plugins():
    registry.load_plugins()
    assert isinstance(registry.all_tools(), list)


async def test_migrate_fails_loudly_without_files(pool, tmp_path):
    with pytest.raises(RuntimeError, match="no migrations"):
        await migrate.run(pool, tmp_path)


async def test_secret_with_foreign_key_is_treated_as_unset(pool, monkeypatch, caplog):
    """A restore on a new machine without the old .env must not crash every settings read."""
    import base64

    from iler.config import get_settings

    await settings_store.put(
        "notify", NotifySettings(telegram_bot_token=SecretStr("sk-test-123"), admin_url="https://bot.example")
    )
    monkeypatch.setattr(get_settings(), "secret_key", base64.b64encode(b"z" * 32).decode())
    with caplog.at_level("ERROR"):
        loaded = await settings_store.get("notify", NotifySettings)
    assert loaded.telegram_bot_token is None and loaded.admin_url == "https://bot.example"
    assert "secret key mismatch" in caplog.text


def test_openapi_disabled_by_default(monkeypatch):
    from iler.main import create_app

    monkeypatch.delenv("ILER_OPENAPI", raising=False)
    assert create_app().openapi_url is None
    monkeypatch.setenv("ILER_OPENAPI", "1")
    assert create_app().openapi_url == "/api/openapi.json"


def test_no_api_error_uses_gateway_statuses():
    """Cloudflare (cloud edition) swaps 502/504 bodies from the origin for its own page, so the admin would
    see "origin returned an invalid response" instead of the real reason. Upstream failures use 503/424."""
    import re

    pattern = re.compile(r"ApiError\(\s*(502|504|52\d)\b")
    root = Path(__file__).parent.parent / "iler"
    offenders = [
        f"{p.relative_to(root)}:{i}"
        for p in root.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []
