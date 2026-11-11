"""PII masking before the LLM and restoring in the reply (Task 5)."""

import pytest

from iler.engine.pii import PiiMasker


@pytest.mark.parametrize("phone", ["+7 701 123 45 67", "87011234567", "+7(701)123-45-67", "7011234567"])
def test_phone_masked_and_restored(phone):
    m = PiiMasker()
    masked = m.mask(f"Звоните {phone} вечером")
    assert phone not in masked
    assert "[PHONE_1]" in masked
    assert m.restore(masked) == f"Звоните {phone} вечером"


def test_email_card_iin_masked():
    m = PiiMasker()
    text = "почта aidar.k@mail.kz, карта 4400 4301 2345 6789, ИИН 990101300123"
    masked = m.mask(text)
    for secret in ("aidar.k@mail.kz", "4400 4301 2345 6789", "990101300123"):
        assert secret not in masked
    assert "[EMAIL_1]" in masked and "[CARD_1]" in masked and "[IIN_1]" in masked
    assert m.restore(masked) == text


def test_dates_times_and_small_numbers_kept():
    m = PiiMasker()
    text = "Бронь на 12.11.2026 в 18:00, 2026-11-12, 3 часа, 4 места, стол 12"
    assert m.mask(text) == text


def test_same_value_same_placeholder_across_messages():
    m = PiiMasker()
    a = m.mask("номер +77011234567")
    b = m.mask("повторю: +77011234567, второй 87019998877")
    assert "[PHONE_1]" in a and "[PHONE_1]" in b and "[PHONE_2]" in b


def test_restore_nested_args():
    m = PiiMasker()
    m.mask("я Айдар, +77011234567")
    assert m.restore_obj({"comment": "перезвонить [PHONE_1]", "n": 2, "l": ["[PHONE_1]"]}) == {
        "comment": "перезвонить +77011234567", "n": 2, "l": ["+77011234567"],
    }
