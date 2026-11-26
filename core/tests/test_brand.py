"""ILER_BRAND: brand of the admin UI (GET /api/brand) and of user-facing server texts."""

import pytest

from iler import config
from iler.config import get_settings
from iler.llm.base import public_error


async def test_brand_default_is_iler(client):
    r = await client.get("/api/brand")
    assert r.status_code == 200
    assert r.json() == {"brand": "iler"}


async def test_brand_endpoint_needs_no_session(client):
    # no cookies, no CSRF: the login and setup screens are branded before anyone signs in
    client.cookies.clear()
    assert (await client.get("/api/brand")).status_code == 200


@pytest.mark.parametrize(("value", "expected"), [("nox", "nox"), (" NOX ", "nox"), ("iler", "iler"), ("acme", "iler"), ("", "iler")])
async def test_brand_values(client, monkeypatch, value, expected):
    monkeypatch.setattr(get_settings(), "brand", value)
    assert (await client.get("/api/brand")).json() == {"brand": expected}


def test_brand_names(monkeypatch):
    assert (config.brand_name(), config.brand_company()) == ("ILER", "ILER")
    monkeypatch.setattr(get_settings(), "brand", "nox")
    assert (config.brand_name(), config.brand_company()) == ("Nox", "Nox service")


def test_key_rejected_text_follows_brand(monkeypatch):
    err = "openai HTTP 401: invalid key sk-...abcd"
    assert public_error(err) == "openai: Ключ ILER отклонён провайдером — обратитесь в ILER"
    monkeypatch.setattr(get_settings(), "brand", "nox")
    assert public_error(err) == "openai: Ключ Nox отклонён провайдером — обратитесь в Nox service"


def test_brand_env_name(monkeypatch):
    original = config.get_settings()
    monkeypatch.setenv("ILER_BRAND", "nox")
    config.get_settings.cache_clear()
    try:
        assert config.brand() == "nox"
    finally:
        # put the original Settings object back into the cache (other tests patch its attributes)
        monkeypatch.delenv("ILER_BRAND")
        with monkeypatch.context() as m:
            m.setattr(config, "Settings", lambda: original)
            config.get_settings.cache_clear()
            assert config.get_settings() is original
    assert config.brand() == "iler"
