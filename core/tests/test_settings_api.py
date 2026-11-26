"""Settings API (masking, secret preservation, LLM key test) and templates endpoint (Task 2)."""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from iler import settings_store
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


async def test_settings_llm_has_no_keys_and_ignores_keys_sent(owner):
    """API keys are ILER's (environment); the owner can neither see nor set them."""
    r = await owner.get("/api/settings/llm")
    assert r.status_code == 200
    body = r.json()
    assert not any(k.endswith("_key") for k in body)
    r = await owner.put("/api/settings/llm", json=body | {"openai_model": "gpt-5.5", "openai_key": KEY,
                                                          "anthropic_key": "sk-ant-x"}, headers=csrf(owner))
    assert r.status_code == 200, r.text
    assert KEY not in r.text and not any(k.endswith("_key") for k in r.json())
    stored = await settings_store.get("llm", LlmSettings)
    assert stored.openai_model == "gpt-5.5" and not hasattr(stored, "openai_key")


async def test_settings_llm_providers_are_those_with_ilER_keys(owner, monkeypatch):
    from iler.config import get_settings

    cfg = get_settings()
    monkeypatch.setattr(cfg, "anthropic_key", "sk-ant-vendor")
    monkeypatch.setattr(cfg, "openai_key", "")
    monkeypatch.setattr(cfg, "gemini_key", "AIza-vendor")
    r = await owner.get("/api/settings/llm/providers")
    assert r.json() == {"providers": ["anthropic", "gemini"]}


async def test_settings_llm_effort_validation(owner):
    body = (await owner.get("/api/settings/llm")).json()
    assert body["anthropic_model"] == "claude-sonnet-5-5" and body["anthropic_effort"] == "low"
    r = await owner.put("/api/settings/llm", json={**body, "anthropic_effort": "high"}, headers=csrf(owner))
    assert r.status_code == 200, r.text
    assert (await settings_store.get("llm", LlmSettings)).anthropic_effort == "high"
    r = await owner.put("/api/settings/llm", json={**body, "anthropic_effort": "max"}, headers=csrf(owner))
    assert r.status_code == 422


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
    await settings_store.put("llm", LlmSettings())
    seen: list[LlmSettings] = []

    async def fake_check(settings: LlmSettings) -> None:
        seen.append(settings)
        if settings.openai_model == "broken":
            raise LlmError("invalid api key", retryable=False)
        if settings.openai_model == "revoked":
            raise LlmError("openai HTTP 401: Incorrect API key provided: sk-pr***wxyz", retryable=False)

    app.dependency_overrides[get_key_checker] = lambda: fake_check
    try:
        r = await owner.post("/api/settings/llm/test", headers=csrf(owner))
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}

        # unsaved form values are tested too
        body = (await owner.get("/api/settings/llm")).json() | {"openai_model": "broken"}
        r = await owner.post("/api/settings/llm/test", json=body, headers=csrf(owner))
        assert r.status_code == 200
        assert r.json() == {"ok": False, "error": "invalid api key"}
        assert seen[-1].openai_model == "broken"
        r = await owner.post("/api/settings/llm/test", json=body | {"openai_model": "revoked"}, headers=csrf(owner))
        assert r.json() == {"ok": False, "error": "openai: Провайдер модели отклонил ключ доступа. Обратитесь к поставщику бота."}
        assert "wxyz" not in r.text
        # testing does not save
        assert (await settings_store.get("llm", LlmSettings)).openai_model != "broken"
    finally:
        app.dependency_overrides.pop(get_key_checker, None)


async def test_openapi_exposes_secrets_as_strings(client):
    r = await client.get("/api/openapi.json")
    assert r.status_code == 200
    schemas = r.json()["components"]["schemas"]
    key = schemas["SmtpSettingsPublic"]["properties"]["password"]
    assert {"type": "string"} in key["anyOf"]
    assert "SmtpSettingsPublic" in schemas


async def test_bot_settings_prompt_fields_and_hours_validation(owner):
    body = (await owner.get("/api/settings/bot")).json()
    assert body["prompt"] == "" and body["template_id"] is None and body["template_values"] == {}
    assert body["prompt_edited"] is False and body["enabled"] is True
    for removed in ("persona", "instructions", "services", "faq", "address"):
        assert removed not in body
    body |= {"template_id": "generic", "template_values": {"business_name": "Мастер"}, "prompt": "Ты — консультант.",
             "prompt_edited": True}
    r = await owner.put("/api/settings/bot", json=body, headers=csrf(owner))
    assert r.status_code == 200, r.text
    stored = await settings_store.get("bot", BotSettings)
    assert stored.prompt == "Ты — консультант." and stored.template_values == {"business_name": "Мастер"}
    assert (await owner.put("/api/settings/bot", json=body | {"prompt": "x" * 20001},
                            headers=csrf(owner))).status_code == 422
    assert (await owner.put("/api/settings/bot", json=body | {"template_values": {"a": "x" * 4001}},
                            headers=csrf(owner))).status_code == 422
    for bad in ("", "25:00", "9:00", "12:60", "24:01", "ab"):
        hours = body["hours"] | {"mon": {"open": bad, "close": "18:00", "closed": False}}
        r = await owner.put("/api/settings/bot", json=body | {"hours": hours}, headers=csrf(owner))
        assert r.status_code == 422, bad
    hours = body["hours"] | {"mon": {"open": "00:00", "close": "24:00", "closed": False},
                             "tue": {"open": "09:30", "close": "23:59", "closed": False}}
    assert (await owner.put("/api/settings/bot", json=body | {"hours": hours}, headers=csrf(owner))).status_code == 200


def test_legacy_bot_settings_still_validate():
    bot = BotSettings.model_validate({"name": "Старый", "persona": "p", "services": [{"name": "VIP"}], "address": "a"})
    assert bot.name == "Старый" and bot.prompt == "" and "persona" not in bot.model_dump()


async def test_templates_list(owner):
    r = await owner.get("/api/templates")
    assert r.status_code == 200
    items = r.json()
    assert {t["id"] for t in items} == {"computer_club", "beauty_salon", "clinic", "generic"}
    club = next(t for t in items if t["id"] == "computer_club")
    assert club["name"] == "Компьютерный клуб" and club["description"]
    first = club["fields"][0]
    assert first["key"] == "business_name" and first["required"] is True and first["type"] == "text"
    assert "prompt" not in club


async def test_templates_require_auth(client):
    assert (await client.get("/api/templates")).status_code == 401


async def test_template_render(owner):
    values = {"business_name": "Борода", "address": "ул. Абая 1", "services": "Стрижка — 5000 ₸", "phone": ""}
    r = await owner.post("/api/templates/beauty_salon/render", json={"values": values}, headers=csrf(owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "«Борода»" in body["prompt"] and "Стрижка — 5000 ₸" in body["prompt"]
    assert "Телефон:" not in body["prompt"]
    assert body["missing"] == ["hours"]
    assert (await settings_store.get("bot", BotSettings)).prompt == ""  # nothing is saved


async def test_template_render_validation(owner):
    url = "/api/templates/generic/render"
    r = await owner.post(url, json={"values": {"evil": "x"}}, headers=csrf(owner))
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = await owner.post(url, json={"values": {"business_name": "x" * 4001}}, headers=csrf(owner))
    assert r.status_code == 422
    r = await owner.post(url, json={"values": {"business_name": "A\x07B"}}, headers=csrf(owner))
    assert r.status_code == 200 and "«AB»" in r.json()["prompt"]
    assert sorted(r.json()["missing"]) == ["activity", "services"]
    assert (await owner.post("/api/templates/nope/render", json={"values": {}}, headers=csrf(owner))).status_code == 404
    r = await owner.post("/api/templates/..%2Fsecrets/render", json={"values": {}}, headers=csrf(owner))
    assert r.status_code == 404
    assert (await owner.post(url, json={"values": {}})).status_code == 403  # CSRF


async def test_template_apply_endpoint_removed(owner):
    r = await owner.post("/api/templates/computer_club/apply", headers=csrf(owner))
    assert r.status_code in (404, 405)




async def test_settings_llm_models_lists_for_unsaved_settings(owner, app):
    from iler.api.settings import get_model_lister

    await settings_store.put("llm", LlmSettings())
    seen: list[LlmSettings] = []

    async def fake_list(settings: LlmSettings) -> list[str]:
        seen.append(settings)
        if settings.provider == "gemini":
            raise LlmError("gemini HTTP 400: API key not valid", retryable=False)
        return ["gpt-5.4-mini", "gpt-5.4-nano"]

    app.dependency_overrides[get_model_lister] = lambda: fake_list
    try:
        r = await owner.post("/api/settings/llm/models", headers=csrf(owner))
        assert r.json() == {"ok": True, "models": ["gpt-5.4-mini", "gpt-5.4-nano"]}
        body = (await owner.get("/api/settings/llm")).json() | {"provider": "gemini"}
        r = await owner.post("/api/settings/llm/models", json=body, headers=csrf(owner))
        # the provider's auth error (may echo a masked fragment of ILER's key) is replaced by a generic text
        assert r.json() == {"ok": False, "models": [], "error": "gemini: Провайдер модели отклонил ключ доступа. Обратитесь к поставщику бота."}
        assert (await settings_store.get("llm", LlmSettings)).provider == "openai"  # nothing saved
        assert (await owner.post("/api/settings/llm/models")).status_code == 403  # CSRF
    finally:
        app.dependency_overrides.pop(get_model_lister, None)


async def test_claude_model_limited_to_sonnet_and_haiku(owner):
    body = (await owner.get("/api/settings/llm")).json()
    assert body["anthropic_model"] == "claude-sonnet-5-5"
    r = await owner.put("/api/settings/llm", json=body | {"anthropic_model": "claude-haiku-5-5"}, headers=csrf(owner))
    assert r.status_code == 200 and r.json()["anthropic_model"] == "claude-haiku-5-5"
    r = await owner.put("/api/settings/llm", json=body | {"anthropic_model": "gpt-5.4-mini"}, headers=csrf(owner))
    assert r.status_code == 422
    # legacy stored value (before the restriction) reads back as Sonnet
    assert LlmSettings.model_validate({"anthropic_model": "claude-opus-5-5"}).anthropic_model == "claude-sonnet-5-5"
