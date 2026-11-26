"""Tariffs: the only place where names, monthly prices (KZT) and monthly coin limits live.

Cloud prices and coin limits from the sales price list v5 (2026-11); changing them
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
    Tariff("start", "Старт", 70000, 30000, "~25–30 диалогов в день: цены, график, адрес, запись"),
    Tariff("business", "Бизнес", 100000, 60000, "~50–55 диалогов в день: салон, клиника, сервис с записью"),
    Tariff("pro", "Про", 160000, 120000, "~100–110 диалогов в день: сеть филиалов, большой поток"),
)

# Free test tariff: no invoice, approved at once, one grant of coins for the trial (NOX_FREE_DAYS), no monthly
# renewal. Not in TARIFFS (paid tariffs only); listed for clients after them when NOX_FREE_TARIFF_PUBLIC=true.
FREE = Tariff("free", "Free (тест)", 0, 2000, "Пробный тариф для теста бота")

BY_CODE = {t.code: t for t in (*TARIFFS, FREE)}

# Extra coin pack bought on top of the monthly coins (cabinet "Пополнить"). Not a subscription tariff:
# get() does not return it. Top-up coins do not expire at the period end (monthly coins do).
TOPUP = Tariff("topup", "Пакет коинов", 9900, 10000, "10 000 коинов сверх месячного пакета, не сгорают")


def get(code: str | None) -> Tariff | None:
    return BY_CODE.get(code or "")


def is_free(code: str | None) -> bool:
    return code == FREE.code


def as_dicts(*, include_free: bool = False) -> list[dict]:
    return [asdict(t) for t in (*TARIFFS, FREE)] if include_free else [asdict(t) for t in TARIFFS]


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₸"
