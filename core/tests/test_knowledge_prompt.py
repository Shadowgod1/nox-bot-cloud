"""Services and staff in the system prompt, the client-language rule, Kazakh detection, salon/clinic templates."""

from datetime import datetime

import pytest

from iler import templates_loader
from iler.engine import lang, prompt
from iler.settings_models import BotSettings, ServiceItem, StaffDay, StaffMember

NOW = datetime(2026, 11, 26, 12, 0)
WORK = StaffDay(works=True, open="10:00", close="19:00")


def _salon(**kw) -> BotSettings:
    kw.setdefault("prompt", "Ты — администратор салона «Aru».")
    kw.setdefault("services", [
        ServiceItem(name="Маникюр с покрытием", price="от 6000 ₸", duration_min=90),
        ServiceItem(name="Наращивание ногтей", price="от … ₸", duration_min=150),
    ])
    kw.setdefault("staff", [
        StaffMember(name="Айгерим", role="мастер маникюра", services=["Маникюр с покрытием"], mon=WORK, thu=WORK),
        StaffMember(name="Дина", services=["Наращивание ногтей"], sat=WORK, no_online_booking=True),
    ])
    return BotSettings(**kw)


def test_services_and_staff_are_in_the_system_prompt():
    system = prompt.build_system(_salon(), NOW, "Asia/Almaty")
    assert system.index("## Описание бизнеса") < system.index("## Услуги и цены") < system.index("## Служебное")
    assert "- Маникюр с покрытием — от 6000 ₸, ~1 ч 30 мин" in system
    # the template placeholder price is never quoted to a client
    assert "- Наращивание ногтей — цену уточнит администратор, ~2 ч 30 мин" in system and "…" not in system
    assert "- Айгерим (мастер маникюра) — услуги: Маникюр с покрытием; работает: Пн 10:00–19:00, Чт 10:00–19:00; " \
           "выходные: Вт, Ср, Пт, Сб, Вс" in system
    assert "Дина — услуги: Наращивание ногтей; работает: Сб 10:00–19:00" in system and "онлайн не записывать" in system
    # honest wording: working days are known, the free time is not
    assert "администратор" in prompt.STAFF_RULES and "подтвердит" in prompt.STAFF_RULES
    assert "Никогда не говори, что время свободно" in system


def test_no_knowledge_no_sections():
    system = prompt.build_system(BotSettings(prompt="Ты — бот."), NOW, "Asia/Almaty")
    assert "## Услуги и цены" not in system and "## Мастера" not in system


def test_staff_without_days_and_every_day():
    every = {d: WORK for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    assert prompt.staff_days_text(StaffMember(name="А")) == "дни работы не указаны"
    assert prompt.staff_days_text(StaffMember(name="Б", **every)).endswith("(без выходных)")


@pytest.mark.parametrize("price,expected", [("5000 ₸", "5000 ₸"), ("от … ₸", "цену уточнит администратор"),
                                            ("", "цену уточнит администратор"), ("договорная", "цену уточнит администратор")])
def test_price_text(price, expected):
    assert prompt.price_text(price) == expected


def test_language_rule_answers_in_kazakh():
    base = prompt.BASE_PROMPT
    assert "на языке последнего сообщения клиента" in base
    assert "по-казахски" in base and "даже если описание бизнеса" in base
    assert "Никогда не придумывай, что в них" in base  # voice / photo are not guessed


@pytest.mark.parametrize("text,expected", [
    ("Сәлеметсіз бе, маникюр қанша тұрады?", "kk"),
    ("Салем, канша турады маникюр?", "kk"),
    ("Рахмет", "kk"),
    ("Сколько стоит маникюр?", "ru"),
    ("Можно к Айгерим в четверг после 6?", "ru"),
    ("👍", None),
    ("", None),
])
def test_detect_language(text, expected):
    assert lang.detect(text) == expected


def test_detect_recent_prefers_latest():
    assert lang.detect_recent(["Здравствуйте", "Қанша тұрады?"]) == "kk"
    assert lang.detect_recent(["Қанша тұрады?", "а по-русски можно?"]) == "ru"


@pytest.mark.parametrize("tid", ["beauty_salon", "clinic"])
def test_salon_and_clinic_templates_prefill(tid):
    t = templates_loader.load(tid)
    keys = {f.key: f for f in t.fields}
    assert keys["tone"].default and keys["faq"].default
    services = BotSettings.model_validate(t.defaults).services
    assert len(services) >= 6 and all(s.price == "от … ₸" and s.duration_min for s in services)
    assert t.defaults["greeting"]
    # rendering with the prefilled values keeps FAQ / tone in the prompt
    values = {f.key: f.default for f in t.fields if f.default}
    values |= {"business_name": "X", "address": "A", "hours": "10–20", "phone": "+7 700 000 00 00"}
    out = templates_loader.render(t.prompt, values)
    assert "Частые вопросы и ответы:" in out and "Тон общения:" in out


def test_legacy_free_text_services_become_rows():
    bot = BotSettings.model_validate({"prompt": "x", "services": "Маникюр — 5000\nПедикюр — 7000"})
    assert [(s.name, s.price) for s in bot.services] == [("Маникюр", "5 000 ₸"), ("Педикюр", "7 000 ₸")]
