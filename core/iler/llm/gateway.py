"""LLM gateway mode: the install reaches every provider through the company's gateway with a virtual key.

With ILER_LLM_GATEWAY_URL set, the provider clients use ``<gateway>/llm/<provider>`` as their base URL and
ILER_LLM_GATEWAY_KEY (``nox_vk_...``) as their API key, sent in the provider's native header; the gateway
swaps in the real key and passes requests and responses through unchanged. The gateway is the authority
on coins:

- zero balance: HTTP 402 ``{"error": {"type": "insufficient_coins"}}``;
- revoked or suspended key: HTTP 401 ``{"error": {"type": "key_revoked"}}``;
- balance: ``GET <gateway>/llm/v1/balance`` -> ``{"coins_left", "monthly_limit", "period_end", "status"}``,
  newer gateways add ``"spent"`` (exact spend of the period, the authority over ``limit - coins_left``, which is
  off by the fraction ``coins_left`` drops) and ``"trial_end"`` (a Free test stops then, before ``period_end``).

After a 402 / 401 of that kind the bot stops calling the LLM (``blocked()``) until a balance read made
after the block shows coins again. The balance is cached for BALANCE_TTL_S (errors too, so a gateway that
is down is not hammered); a failed read keeps the last known value and marks it stale.

The virtual key never appears in logs, errors or API responses.
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from iler import config

log = logging.getLogger("iler.llm.gateway")

PROVIDER_PATHS = {"anthropic": "/llm/anthropic", "openai": "/llm/openai/v1", "gemini": "/llm/gemini"}
GEMINI_API_VERSION = "v1beta"  # the gateway's Gemini route is /llm/gemini/v1beta/...
BALANCE_PATH = "/llm/v1/balance"
INSUFFICIENT_COINS = "insufficient_coins"
KEY_REVOKED = "key_revoked"
BALANCE_TTL_S = 60.0
BALANCE_TIMEOUT_S = 5.0
_REVOKED_STATUSES = frozenset({"revoked", "suspended", "blocked", "disabled"})


def enabled() -> bool:
    return config.gateway_mode()


def root() -> str:
    return config.get_settings().llm_gateway_url.strip().rstrip("/")


def key() -> str:
    return config.get_settings().llm_gateway_key.strip()


def base_url(provider: str) -> str:
    """Base URL of a provider client in gateway mode (the SDK appends its own /v1/messages etc.)."""
    return root() + PROVIDER_PATHS[provider]


def blocking_error(err: BaseException) -> str | None:
    """INSUFFICIENT_COINS / KEY_REVOKED when an LLM error is the gateway refusing the install, else None.

    Only in gateway mode: a direct provider never answers with these types."""
    if not enabled():
        return None
    error_type = getattr(err, "error_type", None)
    status = getattr(err, "status", None)
    if error_type == KEY_REVOKED:
        return KEY_REVOKED
    if error_type == INSUFFICIENT_COINS or status == 402:
        return INSUFFICIENT_COINS
    return None


@dataclass
class Balance:
    coins_left: float
    monthly_limit: float
    period_end: str | None
    status: str
    # providers the gateway has a company key for; None = an older gateway that does not say (all of them)
    providers: tuple[str, ...] | None = None
    spent: float | None = None  # exact spend of the period; None = an older gateway (use limit - coins_left)
    trial_end: str | None = None  # Free test: the account stops at this moment; None = not a test

    @property
    def revoked(self) -> bool:
        return self.status.strip().lower() in _REVOKED_STATUSES

    @property
    def usable(self) -> bool:
        return self.coins_left > 0 and not self.revoked


@dataclass
class BalanceView:
    balance: Balance | None  # last known value (None: never read successfully)
    fetched_at: datetime | None  # when ``balance`` was read
    stale: bool  # the latest read failed (or there was none yet): ``balance`` may be outdated
    blocked: str | None  # INSUFFICIENT_COINS / KEY_REVOKED while the bot is not calling the LLM


class _State:
    def __init__(self) -> None:
        self.balance: Balance | None = None
        self.fetched_at: datetime | None = None
        self.reads = 0  # successful reads so far
        self.attempt_mono: float | None = None  # monotonic time of the last read attempt
        self.error: str | None = None
        self.blocked: str | None = None
        self.blocked_after = 0  # value of ``reads`` when the block was set: only a later read lifts it


_state = _State()
_transport: httpx.AsyncBaseTransport | None = None  # test hook


def reset(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Forget the cached balance and the block (tests); ``transport`` mocks the balance HTTP calls."""
    global _state, _transport
    _state = _State()
    _transport = transport


def mark_blocked(kind: str) -> None:
    """The gateway refused a call (402 / 401 key_revoked): no LLM calls until the balance is back."""
    if _state.blocked != kind:
        log.warning("LLM gateway refused the install (%s): the bot hands conversations to the operator", kind)
    _state.blocked = kind
    _state.blocked_after = _state.reads


def current_block() -> str | None:
    return _state.blocked if enabled() else None


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("not a number")
    return float(value)


async def _fetch() -> Balance:
    secret = key()
    headers = {"Authorization": f"Bearer {secret}", "x-api-key": secret}
    async with httpx.AsyncClient(transport=_transport, timeout=BALANCE_TIMEOUT_S) as client:
        resp = await client.get(root() + BALANCE_PATH, headers=headers)
    if resp.status_code == 401:
        try:
            error_type = (resp.json().get("error") or {}).get("type")
        except (ValueError, AttributeError):
            error_type = None
        if error_type == KEY_REVOKED:
            last = _state.balance
            return Balance(coins_left=last.coins_left if last else 0.0,
                           monthly_limit=last.monthly_limit if last else 0.0,
                           period_end=last.period_end if last else None, status="revoked",
                           spent=last.spent if last else None, trial_end=last.trial_end if last else None)
    if resp.status_code != 200:
        raise RuntimeError(f"balance HTTP {resp.status_code}")
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("balance is not an object")
    period_end = data.get("period_end")
    trial_end = data.get("trial_end")
    try:
        spent = _number(data["spent"]) if data.get("spent") is not None else None
    except ValueError:
        spent = None
    raw_providers = data.get("providers")
    providers = (tuple(p for p in raw_providers if isinstance(p, str))
                 if isinstance(raw_providers, list) else None)
    return Balance(
        providers=providers,
        coins_left=_number(data.get("coins_left")),
        monthly_limit=_number(data.get("monthly_limit") or 0),
        period_end=str(period_end) if period_end else None,
        status=str(data.get("status") or "active"),
        spent=spent,
        trial_end=str(trial_end) if trial_end else None,
    )


async def balance(*, force: bool = False) -> BalanceView:
    """The gateway balance, cached for BALANCE_TTL_S; never raises (a failed read marks the view stale)."""
    if not enabled():
        return BalanceView(balance=None, fetched_at=None, stale=True, blocked=None)
    now = time.monotonic()
    if force or _state.attempt_mono is None or now - _state.attempt_mono >= BALANCE_TTL_S:
        _state.attempt_mono = now
        try:
            value = await _fetch()
        except Exception as e:  # noqa: BLE001 - the dashboard and the bot keep working without it
            _state.error = type(e).__name__
            log.warning("LLM gateway balance unavailable: %s", _state.error)
        else:
            _state.balance = value
            _state.fetched_at = datetime.now(UTC)
            _state.reads += 1
            _state.error = None
            if value.revoked:
                mark_blocked(KEY_REVOKED)
            elif _state.blocked and value.usable and _state.reads > _state.blocked_after:
                log.info("LLM gateway balance is back (%s left): the bot answers again", value.coins_left)
                _state.blocked = None
    return BalanceView(balance=_state.balance, fetched_at=_state.fetched_at,
                       stale=_state.error is not None or _state.balance is None, blocked=_state.blocked)


def known_providers() -> tuple[str, ...] | None:
    """Providers the gateway can serve, from the last balance read; None while unknown (then all)."""
    if not enabled() or _state.balance is None:
        return None
    return _state.balance.providers


async def blocked() -> str | None:
    """Checked before each bot turn: INSUFFICIENT_COINS / KEY_REVOKED when the LLM must not be called.

    A block set by a gateway refusal holds until a balance read made after it shows coins (and an active
    key); without a block, a fresh balance of zero blocks too. A failed balance read never blocks by
    itself: the gateway answers 402 if the coins are really gone."""
    if not enabled():
        return None
    view = await balance()
    if view.blocked:
        return view.blocked
    b = view.balance
    if b is not None and not view.stale:
        if b.revoked:
            return KEY_REVOKED
        if b.coins_left <= 0:
            return INSUFFICIENT_COINS
    return None
