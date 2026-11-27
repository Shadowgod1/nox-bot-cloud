"""Tariffs: the only place where names, monthly prices (KZT) and monthly coin limits live.

Cloud prices and coin limits from the sales price list v6 (27.11.2026: 59 990 / 89 990 / 149 990); changing them
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
    Tariff("start", "Старт", 59990, 30000, "~50 диалогов в день: цены, график, адрес, запись"),
    Tariff("business", "Бизнес", 89990, 60000, "~100 диалогов в день: салон, клиника, сервис с записью"),
    Tariff("pro", "Про", 149990, 120000, "~200 диалогов в день: сеть филиалов, большой поток"),
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


def dialogs(coins: float) -> int:
    """About how many client dialogs the coins pay for (NOX_COINS_PER_DIALOG coins per dialog), rounded to a
    number that reads as an estimate: 54 -> 50, 811 -> 800, 1622 -> 1600. Under 10 the exact count."""
    from nox_portal.config import get_settings

    per = max(get_settings().coins_per_dialog, 1)
    n = max(coins, 0) / per
    if n < 10:
        return int(n)
    step = 10 if n < 100 else 50 if n < 1000 else 100
    return int(round(n / step) * step)


def _out(t: Tariff) -> dict:
    return {**asdict(t), "dialogs": dialogs(t.coins)}


def as_dicts(*, include_free: bool = False) -> list[dict]:
    return [_out(t) for t in ((*TARIFFS, FREE) if include_free else TARIFFS)]


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₸"
