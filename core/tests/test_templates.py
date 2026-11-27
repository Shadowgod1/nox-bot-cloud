"""Shipped bot templates and the template renderer."""

import re

import pytest

from iler import templates_loader
from iler.settings_models import BotSettings

SHIPPED = ["beauty_salon", "clinic", "computer_club", "generic"]
COMMON_RULES = ("create_booking_request", "handover_to_operator", "WhatsApp", "казах", "инструкции")


def _full_values(t: templates_loader.Template) -> dict[str, str]:
    return {f.key: f"<{f.key}>" for f in t.fields}


def test_all_shipped_templates_listed():
    metas = templates_loader.list_templates()
    assert [m.id for m in metas][-1] == "generic"
    assert sorted(m.id for m in metas) == SHIPPED


@pytest.mark.parametrize("tid", SHIPPED)
def test_shipped_template_is_valid(tid):
    t = templates_loader.load(tid)
    assert t.id == tid and t.name and t.description
    keys = [f.key for f in t.fields]
    assert keys[0] == "business_name" and keys[-1] == "extra"
    assert {"business_name", "phone", "extra"} <= set(keys)
    BotSettings.model_validate(t.defaults)
    full = templates_loader.render(t.prompt, _full_values(t))
    for rule in COMMON_RULES:
        assert rule in full, f"{tid}: prompt lacks {rule!r}"
    for key in keys:
        assert f"<{key}>" in full, f"{tid}: field {key} is never used in the prompt"
    assert "{{" not in full and "}}" not in full
    assert len(full) < 20000


@pytest.mark.parametrize("tid", SHIPPED)
def test_optional_fields_leave_no_dangling_labels(tid):
    t = templates_loader.load(tid)
    values = {f.key: f"<{f.key}>" for f in t.fields if f.required}
    out = templates_loader.render(t.prompt, values)
    assert "{{" not in out and "\n\n\n" not in out
    assert templates_loader.missing_required(t, values) == []
    lines = out.splitlines()
    for i, line in enumerate(lines):
        assert not re.match(r"^(Телефон|Оплата|Адрес|Часы работы)[^:]*:\s*$", line), line
        if line.rstrip().endswith(":") and not line.startswith(" "):  # a block label must have content below
            assert i + 1 < len(lines) and lines[i + 1].strip(), f"{tid}: empty block {line!r}"
    assert "Дополнительно:" not in out and " ;" not in out


def test_computer_club_books_via_request_without_seat_lookup():
    t = templates_loader.load("computer_club")
    assert "create_booking_request" in t.prompt and "администратор подтвердит наличие" in t.prompt
    assert "free_workstations" not in t.prompt and "свободным местам" not in t.defaults.get("greeting", "")


def test_clinic_never_gives_medical_advice():
    p = templates_loader.load("clinic").prompt
    assert "диагноз" in p and "103" in p and "handover_to_operator" in p


def test_renderer_sections_and_vars():
    tpl = "A {{name}}.{{#phone}} Тел: {{phone}}.{{/phone}}{{^phone}} Без телефона.{{/phone}}\n\n\n\nB {{ unknown }}"
    assert templates_loader.render(tpl, {"name": "X", "phone": "1"}) == "A X. Тел: 1.\n\nB"
    assert templates_loader.render(tpl, {"name": "X", "phone": "  "}) == "A X. Без телефона.\n\nB"


def test_renderer_values_cannot_inject_markup():
    out = templates_loader.render("{{a}} {{#b}}secret{{/b}}", {"a": "{{#b}}x{{/b}} {{b}}", "b": ""})
    assert out == "{{#b}}x{{/b}} {{b}}"


def test_nested_sections():
    tpl = "{{#a}}A{{#b}}B{{/b}}{{/a}}"
    assert templates_loader.render(tpl, {"a": "1", "b": "1"}) == "AB"
    assert templates_loader.render(tpl, {"a": "1"}) == "A"
    assert templates_loader.render(tpl, {"b": "1"}) == ""


def test_clean_values():
    t = templates_loader.load("generic")
    assert templates_loader.clean_values(t, {"business_name": "A\x00B\tC\r\nD\x1b"}) == {"business_name": "AB C\nD"}
    with pytest.raises(templates_loader.ValuesInvalid):
        templates_loader.clean_values(t, {"nope": "x"})
    with pytest.raises(templates_loader.ValuesInvalid):
        templates_loader.clean_values(t, {"business_name": "x" * 4001})
    with pytest.raises(templates_loader.ValuesInvalid):
        templates_loader.clean_values(t, {"business_name": 5})


def test_load_rejects_traversal_and_missing():
    for bad in ("../secrets", "..", "a/b", "A", "", "missing"):
        with pytest.raises(templates_loader.TemplateNotFound):
            templates_loader.load(bad)


def test_invalid_template_files_are_skipped(tmp_path, monkeypatch):
    (tmp_path / "ok.yaml").write_text("name: OK\nprompt: Привет {{business_name}}\n"
                                      "fields:\n  - {key: business_name, label: Название}\n", encoding="utf-8")
    (tmp_path / "noprompt.yaml").write_text("name: X\n", encoding="utf-8")
    (tmp_path / "baddefaults.yaml").write_text("name: X\nprompt: p\ndefaults: {buffer_delay_seconds: 1000}\n",
                                               encoding="utf-8")
    (tmp_path / "badkey.yaml").write_text("name: X\nprompt: p\nfields:\n  - {key: Bad-Key, label: L}\n", encoding="utf-8")
    (tmp_path / "promptdefault.yaml").write_text("name: X\nprompt: p\ndefaults: {prompt: hacked}\n", encoding="utf-8")
    monkeypatch.setattr(templates_loader, "TEMPLATES_DIR", tmp_path)
    assert [m.id for m in templates_loader.list_templates()] == ["ok"]
    with pytest.raises(templates_loader.TemplateInvalid):
        templates_loader.load("noprompt")
