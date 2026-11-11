"""Settings API (masking, secret preservation, LLM key test) and templates endpoint (Task 2)."""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from iler import settings_store, templates_loader
from iler.api.settings import get_key_checker
from iler.llm.base import LlmError
from iler.settings_models import BotSettings, LlmSettings, NotifySettings

OWNER = {"email": "owner@club.kz", "password": "owner-pass-123", "name": "Owner"}
KEY = "sk-live-0123456789abcd"


def csrf(c: AsyncClient) -> dict:
    return {"X-ILER-CSRF": c.cookies.get("iler_csrf", "")}


@pytest.fixture
async def owner(client):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    return client


async def test_settings_require_auth(client):
    assert (await client.get("/api/settings/bot")).status_code == 401
    assert (await client.get("/api/settings/llm")).status_code == 401


async def test_settings_llm_get_masks_keys(owner):
    await settings_store.put("llm", LlmSettings(openai_key=SecretStr(KEY)))
    r = await owner.get("/api/settings/llm")
    assert r.status_code == 200
    body = r.json()
    assert body["openai_key"] == "••••abcd"
    assert body["gemini_key"] is None
    assert KEY not in r.text


async def test_put_settings_keeps_secret_when_masked_value_sent(owner):
    await settings_store.put("llm", LlmSettings(openai_key=SecretStr(KEY), gemini_key=SecretStr("g-key-999999")))
    body = (await owner.get("/api/settings/llm")).json()
    body["openai_model"] = "gpt-4.1"
    body.pop("gemini_key")  # omitted secret is kept as well
    r = await owner.put("/api/settings/llm", json=body, headers=csrf(owner))
    assert r.status_code == 200, r.text
    assert r.json()["openai_key"] == "••••abcd"
    stored = await settings_store.get("llm", LlmSettings)
    assert stored.openai_model == "gpt-4.1"
    assert stored.openai_key.get_secret_value() == KEY
    assert stored.gemini_key.get_secret_value() == "g-key-999999"

    # a new value replaces, null/"" clears
    body = {**body, "openai_key": "sk-new-key-0000wxyz", "gemini_key": ""}
    r = await owner.put("/api/settings/llm", json=body, headers=csrf(owner))
    assert r.status_code == 200, r.text
    stored = await settings_store.get("llm", LlmSettings)
    assert stored.openai_key.get_secret_value() == "sk-new-key-0000wxyz"
    assert stored.gemini_key is None

    # masked value with nothing stored does not become a secret
    r = await owner.put("/api/settings/llm", json={**body, "gemini_key": "••••abcd"}, headers=csrf(owner))
    assert (await settings_store.get("llm", LlmSettings)).gemini_key is None


async def test_notify_smtp_password_masked_and_kept(owner):
    payload = {
        "admin_phones": ["77011234567"],
        "admin_group_jid": None,
        "smtp": {"host": "smtp.example.org", "user": "bot", "password": "smtp-secret-1234", "to": ["a@b.kz"]},
    }
    r = await owner.put("/api/settings/notify", json=payload, headers=csrf(owner))
    assert r.status_code == 200, r.text
    assert r.json()["smtp"]["password"] == "••••1234"
    got = (await owner.get("/api/settings/notify")).json()
    assert "smtp-secret-1234" not in str(got)
    got["admin_phones"] = ["77019999999"]
    r = await owner.put("/api/settings/notify", json=got, headers=csrf(owner))
    assert r.status_code == 200
    stored = await settings_store.get("notify", NotifySettings)
    assert stored.smtp.password.get_secret_value() == "smtp-secret-1234"
    assert stored.admin_phones == ["77019999999"]


async def test_notify_masked_password_rejected_when_smtp_target_changes(owner):
    payload = {"smtp": {"host": "smtp.example.org", "user": "bot", "password": "smtp-secret-1234", "to": ["a@b.kz"]}}
    assert (await owner.put("/api/settings/notify", json=payload, headers=csrf(owner))).status_code == 200
    got = (await owner.get("/api/settings/notify")).json()
    for change in ({"host": "evil.example.com"}, {"port": 2525}, {"user": "other"}):
        body = {**got, "smtp": {**got["smtp"], **change}}
        r = await owner.put("/api/settings/notify", json=body, headers=csrf(owner))
        assert r.status_code == 422, (change, r.text)
        assert r.json()["error"]["code"] == "smtp_password_required"
        # an omitted password is treated like a masked one
        body["smtp"].pop("password")
        r = await owner.put("/api/settings/notify", json=body, headers=csrf(owner))
        assert r.status_code == 422
    stored = await settings_store.get("notify", NotifySettings)
    assert stored.smtp.host == "smtp.example.org"
    # re-entering the password together with the new host works
    body = {**got, "smtp": {**got["smtp"], "host": "smtp2.example.org", "password": "new-secret-5678"}}
    r = await owner.put("/api/settings/notify", json=body, headers=csrf(owner))
    assert r.status_code == 200, r.text
    stored = await settings_store.get("notify", NotifySettings)
    assert stored.smtp.host == "smtp2.example.org"
    assert stored.smtp.password.get_secret_value() == "new-secret-5678"


async def test_operator_cannot_touch_notify_settings(owner, app):
    r = await owner.post(
        "/api/users",
        json={"email": "op@club.kz", "password": "operator-pass-1", "name": "Op", "role": "operator"},
        headers=csrf(owner),
    )
    assert r.status_code == 200
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as op:
        await op.post("/api/auth/login", json={"email": "op@club.kz", "password": "operator-pass-1"})
        assert (await op.get("/api/settings/notify")).status_code == 403
        r = await op.put("/api/settings/notify", json={"admin_phones": []}, headers=csrf(op))
        assert r.status_code == 403


async def test_bot_settings_roundtrip_and_validation(owner):
    r = await owner.get("/api/settings/bot")
    assert r.status_code == 200 and r.json()["buffer_delay_seconds"] == 8
    body = r.json() | {"name": "Арена", "buffer_delay_seconds": 15}
    r = await owner.put("/api/settings/bot", json=body, headers=csrf(owner))
    assert r.status_code == 200 and r.json()["name"] == "Арена"
    assert (await settings_store.get("bot", BotSettings)).buffer_delay_seconds == 15
    r = await owner.put("/api/settings/bot", json=body | {"buffer_delay_seconds": 1}, headers=csrf(owner))
    assert r.status_code == 422


async def test_operator_cannot_touch_llm_settings(owner, app):
    r = await owner.post(
        "/api/users",
        json={"email": "op@club.kz", "password": "operator-pass-1", "name": "Op", "role": "operator"},
        headers=csrf(owner),
    )
    assert r.status_code == 200
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as op:
        await op.post("/api/auth/login", json={"email": "op@club.kz", "password": "operator-pass-1"})
        assert (await op.get("/api/settings/llm")).status_code == 403
        assert (await op.get("/api/settings/bot")).status_code == 200


async def test_settings_llm_test_calls_provider(owner, app):
    await settings_store.put("llm", LlmSettings(openai_key=SecretStr(KEY)))
    seen: list[LlmSettings] = []

    async def fake_check(settings: LlmSettings) -> None:
        seen.append(settings)
        if settings.openai_model == "broken":
            raise LlmError("invalid api key", retryable=False)

    app.dependency_overrides[get_key_checker] = lambda: fake_check
    try:
        r = await owner.post("/api/settings/llm/test", headers=csrf(owner))
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}
        assert seen[-1].openai_key.get_secret_value() == KEY

        # unsaved form values are tested too; masked key falls back to the stored one
        body = (await owner.get("/api/settings/llm")).json() | {"openai_model": "broken"}
        r = await owner.post("/api/settings/llm/test", json=body, headers=csrf(owner))
        assert r.status_code == 200
        assert r.json() == {"ok": False, "error": "invalid api key"}
        assert seen[-1].openai_key.get_secret_value() == KEY
        # testing does not save
        assert (await settings_store.get("llm", LlmSettings)).openai_model != "broken"
    finally:
        app.dependency_overrides.pop(get_key_checker, None)


async def test_openapi_exposes_secrets_as_strings(client):
    r = await client.get("/api/openapi.json")
    assert r.status_code == 200
    schemas = r.json()["components"]["schemas"]
    key = schemas["LlmSettingsPublic"]["properties"]["openai_key"]
    assert {"type": "string"} in key["anyOf"]
    assert "SmtpSettingsPublic" in schemas


@pytest.fixture
def template_dir(tmp_path, monkeypatch):
    (tmp_path / "computer_club.yaml").write_text(
        "meta:\n  name: Компьютерный клуб\n  description: Для клубов\n"
        "name: Клуб-бот\npersona: Администратор клуба\nservices:\n  - name: Общий зал\n    price: 500 ₸/ч\n",
        encoding="utf-8",
    )
    (tmp_path / "generic.yaml").write_text("name: Универсальный\n", encoding="utf-8")
    (tmp_path / "broken.yaml").write_text("buffer_delay_seconds: 1000\n", encoding="utf-8")
    monkeypatch.setattr(templates_loader, "TEMPLATES_DIR", tmp_path)
    return tmp_path


def test_templates_loader(template_dir):
    metas = {m.id: m for m in templates_loader.list_templates()}
    assert set(metas) == {"computer_club", "generic"}  # invalid file skipped
    assert metas["computer_club"].name == "Компьютерный клуб"
    assert metas["generic"].name == "generic"
    bot = templates_loader.load("computer_club")
    assert isinstance(bot, BotSettings) and bot.services[0].price == "500 ₸/ч"
    with pytest.raises(templates_loader.TemplateNotFound):
        templates_loader.load("../secrets")
    with pytest.raises(templates_loader.TemplateNotFound):
        templates_loader.load("missing")


async def test_templates_apply_overwrites_bot_settings(owner, template_dir):
    await settings_store.put("bot", BotSettings(name="Старый", address="ул. Абая 1"))
    r = await owner.get("/api/templates")
    assert r.status_code == 200
    assert {t["id"] for t in r.json()} == {"computer_club", "generic"}
    r = await owner.post("/api/templates/computer_club/apply", headers=csrf(owner))
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Клуб-бот"
    stored = await settings_store.get("bot", BotSettings)
    assert stored.name == "Клуб-бот" and stored.address == "" and stored.persona == "Администратор клуба"
    assert (await owner.post("/api/templates/nope/apply", headers=csrf(owner))).status_code == 404
    assert (await owner.post("/api/templates/computer_club/apply")).status_code == 403
