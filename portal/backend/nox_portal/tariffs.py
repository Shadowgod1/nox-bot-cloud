"""Tariffs: the only place where names, monthly prices (KZT) and monthly coin limits live.

Prices below are launch placeholders agreed to be confirmed by Nox service before going live; changing them
affects only invoices issued afterwards (an issued invoice keeps its amount in accounts.invoice_amount).
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Tariff:
    code: str
    name: str
    price_kzt: int
    coins: int
    description: str


TARIFFS: tuple[Tariff, ...] = (
    Tariff("start", "Старт", 15000, 30000, "Небольшой поток обращений: салон, кабинет, мастерская"),
    Tariff("business", "Бизнес", 39000, 100000, "Постоянный поток клиентов, несколько услуг и сотрудников"),
    Tariff("pro", "Про", 99000, 300000, "Сеть филиалов или высокий поток сообщений"),
)

BY_CODE = {t.code: t for t in TARIFFS}


def get(code: str | None) -> Tariff | None:
    return BY_CODE.get(code or "")


def as_dicts() -> list[dict]:
    return [asdict(t) for t in TARIFFS]


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₸"
