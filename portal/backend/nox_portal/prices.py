"""Model prices for the LLM gateway: the only price table of the portal.

Mirrors core/iler/usage.py (the bot's own coin accounting): 1 coin = $0.001, prices in $ per 1M tokens
(input, output), the longest matching model prefix wins, and the built-in table holds the same Claude
prices as the core. Overrides come from NOX_MODEL_PRICES in the same JSON format as the core's
ILER_MODEL_PRICES: {"model-prefix": [input, output]}; a 4-element entry [input, output, cache_write,
cache_read] also sets the Anthropic prompt-cache prices (default: 1.25x and 0.1x of input).

A model with no entry is never billed as 0: it is charged at the most expensive known rate of the same
provider (or FALLBACK_PRICE, the core's fallback, when the provider has no entry at all), a warning is
logged once per model and the ledger row is marked priced = false.
"""

import json
import logging
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from nox_portal.config import get_settings

log = logging.getLogger("nox_portal.prices")

COIN_USD = Decimal("0.001")
# Claude: same numbers as core/iler/usage.py DEFAULT_PRICES / FALLBACK_PRICE. GPT / Gemini: the models of
# the admin UI, same as DEFAULT_PRICES in deploy/cloud/iler-client.sh (ILER_MODEL_PRICES of cloud clients).
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "gpt-5.4-mini": (0.75, 4.5),
    "gemini-3.8-flash": (0.75, 3.75),
}
FALLBACK_PRICE = (2.0, 10.0)
# Anthropic prompt caching relative to the input price: 5-minute cache write and cache read
CACHE_WRITE_FACTOR = 1.25
CACHE_READ_FACTOR = 0.1

PROVIDERS = ("anthropic", "openai", "gemini")
_OPENAI_PREFIXES = ("gpt", "chatgpt", "o1", "o3", "o4", "text-embedding", "codex", "computer-use", "omni")


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_write: float
    cache_read: float

    @classmethod
    def of(cls, pair: tuple[float, ...] | list[float]) -> "Price":
        pin, pout = float(pair[0]), float(pair[1])
        # rounded: 0.1 * 0.1 is 0.010000000000000002 in floats and would round a charge up by 0.001
        cw = float(pair[2]) if len(pair) > 2 else round(pin * CACHE_WRITE_FACTOR, 9)
        cr = float(pair[3]) if len(pair) > 3 else round(pin * CACHE_READ_FACTOR, 9)
        if min(pin, pout, cw, cr) < 0:
            raise ValueError("negative price")
        return cls(pin, pout, cw, cr)


@dataclass
class Tokens:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_write + self.cache_read


def provider_of(model_prefix: str) -> str | None:
    """Which provider a price-table entry belongs to (by the model name)."""
    name = model_prefix.lower().removeprefix("models/")
    if name.startswith("claude"):
        return "anthropic"
    if name.startswith("gemini") or name.startswith("gemma"):
        return "gemini"
    if name.startswith(_OPENAI_PREFIXES):
        return "openai"
    return None


_table_cache: tuple[str, dict[str, Price]] | None = None


def table() -> dict[str, Price]:
    """DEFAULT_PRICES + NOX_MODEL_PRICES (parsed once per distinct value)."""
    global _table_cache
    raw = get_settings().model_prices.strip()
    if _table_cache is not None and _table_cache[0] == raw:
        return _table_cache[1]
    out = {prefix: Price.of(pair) for prefix, pair in DEFAULT_PRICES.items()}
    if raw:
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise TypeError("not an object")
            extra = {str(prefix): Price.of(pair) for prefix, pair in parsed.items()}
            out.update(extra)
        except (ValueError, TypeError, IndexError, AttributeError):
            log.error('NOX_MODEL_PRICES is not valid JSON {"model": [in, out]}; using the built-in prices')
    _table_cache = (raw, out)
    return out


_warned: set[tuple[str, str]] = set()


def price_for(provider: str, model: str) -> tuple[Price, bool]:
    """(price, priced). priced = False when the model is unknown and the provider's top rate is used."""
    prices = table()
    name = (model or "").removeprefix("models/")
    candidates = [p for p in prices if provider_of(p) in (provider, None) and name.startswith(p)]
    if candidates:
        return prices[max(candidates, key=len)], True
    same = [prices[p] for p in prices if provider_of(p) == provider]
    price = max(same, key=lambda p: (p.output, p.input)) if same else Price.of(FALLBACK_PRICE)
    if (provider, name) not in _warned:
        _warned.add((provider, name))
        log.warning("model %r of %s has no price (NOX_MODEL_PRICES): billed at %s/%s $ per 1M tokens",
                    name or "?", provider, price.input, price.output)
    return price, False


def usd(price: Price, tokens: Tokens) -> Decimal:
    total = (Decimal(tokens.input) * Decimal(str(price.input)) + Decimal(tokens.output) * Decimal(str(price.output))
             + Decimal(tokens.cache_write) * Decimal(str(price.cache_write))
             + Decimal(tokens.cache_read) * Decimal(str(price.cache_read)))
    return total / Decimal(1_000_000)


def coins(price: Price, tokens: Tokens) -> Decimal:
    """Coins with 3 decimals, rounded up (a request is never billed below its cost)."""
    return (usd(price, tokens) / COIN_USD).quantize(Decimal("0.001"), rounding=ROUND_CEILING)
