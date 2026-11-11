from pathlib import Path

import pytest
import yaml

from iler.settings_models import BotSettings

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "iler" / "templates"


def _load(name: str) -> dict:
    return yaml.safe_load((TEMPLATES_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["computer_club", "generic"])
def test_templates_validate_as_botsettings(name):
    doc = _load(name)
    assert doc["id"] == name
    assert doc["name"] and doc["description"]
    bot = BotSettings.model_validate(doc["settings"])
    assert bot.persona and bot.instructions


def test_computer_club_template_content():
    bot = BotSettings.model_validate(_load("computer_club")["settings"])
    for tool in ("create_booking_request", "senet_free_workstations", "handover_to_operator"):
        assert tool in bot.instructions
    assert bot.hours.always_open is True
    assert len(bot.services) >= 4 and len(bot.faq) >= 4
    assert all("уточните" in s.price.lower() for s in bot.services)
