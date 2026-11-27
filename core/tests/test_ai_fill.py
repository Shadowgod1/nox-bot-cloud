"""AI fill of a bot template from the owner's own words (POST /api/templates/{id}/ai-fill)."""

import json

import pytest

from iler import settings_store
from iler.llm import router
from iler.llm.base import LlmError, LlmResponse
from iler.settings_models import BotSettings

OWNER = {"email": "owner@salon.kz", "password": "owner-pass-123", "name": "Owner"}
STORY = "Салон на Абая 10, работаем каждый день с 10 до 20. Стрижка 5000, маникюр 7000. Пишите на вы."


def csrf(c) -> dict:
    return {"X-ILER-CSRF": c.cookies.get("iler_csrf", "")}


class ScriptedLlm:
    def __init__(self, text: str | Exception):
        self.text = text
        self.calls: list = []

    async def complete(self, messages, tools, model):
        self.calls.append((messages, tools, model))
        if isinstance(self.text, Exception):
            raise self.text
        return LlmResponse(text=self.text, model=model)


@pytest.fixture
async def owner(client):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def llm():
    holder: dict = {}

    def use(text):
        holder["llm"] = ScriptedLlm(text)
        router.set_provider_factory(lambda name, s: holder["llm"])
        return holder["llm"]

    yield use
    router.set_provider_factory(None)


async def test_ai_fill_lays_the_story_over_the_template(owner, llm):
    answer = {"address": "ул. Абая 10", "hours": "Ежедневно 10:00–20:00", "services": "Стрижка — 5000 ₸\nМаникюр — 7000 ₸",
              "business_name": "Чужое имя", "evil": "x", "phone": 12345}
    fake = llm("Вот анкета:\n```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```")
    r = await owner.post("/api/templates/beauty_salon/ai-fill", headers=csrf(owner),
                         json={"business_name": "Aru Beauty", "description": STORY})
    assert r.status_code == 200, r.text
    body = r.json()
    v = body["values"]
    assert v["business_name"] == "Aru Beauty"  # the owner's own name, never the model's
    assert v["address"] == "ул. Абая 10" and "Маникюр — 7000 ₸" in v["services"]
    assert "evil" not in v and "phone" not in v  # unknown keys and non-strings are dropped
    assert v["tone"]  # the template's typical text stays where the owner said nothing
    assert "«Aru Beauty»" in body["prompt"] and "Адрес: ул. Абая 10" in body["prompt"]
    assert body["missing"] == []
    messages, tools, model = fake.calls[0]
    assert tools == [] and model == "claude-haiku-5-5"
    assert STORY in messages[1].content and "address" in messages[1].content
    assert (await settings_store.get("bot", BotSettings)).prompt == ""  # nothing is saved


async def test_ai_fill_garbage_answer_keeps_only_the_name(owner, llm):
    llm("Извините, не понял.")
    r = await owner.post("/api/templates/generic/ai-fill", headers=csrf(owner),
                         json={"business_name": "Мастер", "description": "Ремонт квартир под ключ"})
    assert r.status_code == 200, r.text
    assert r.json()["values"]["business_name"] == "Мастер"
    assert "«Мастер»" in r.json()["prompt"]


async def test_ai_fill_errors(owner, llm):
    url = "/api/templates/generic/ai-fill"
    ok = {"business_name": "Мастер", "description": "Ремонт квартир под ключ"}
    assert (await owner.post(url, json=ok)).status_code == 403  # CSRF
    assert (await owner.post(url, headers=csrf(owner), json=ok | {"description": "ой"})).status_code == 422
    assert (await owner.post("/api/templates/nope/ai-fill", headers=csrf(owner), json=ok)).status_code == 404
    llm(LlmError("anthropic: APIConnectionError", retryable=False))
    r = await owner.post(url, headers=csrf(owner), json=ok)
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_error"


async def test_ai_fill_needs_login(client):
    r = await client.post("/api/templates/generic/ai-fill", json={"business_name": "x", "description": "0123456789"})
    assert r.status_code in (401, 403)
