"""Online payments: provider registry (NOX_PAYMENT_PROVIDER). No provider = the online button is hidden and
clients pay by invoice through a manager."""

import logging

from nox_portal.config import get_settings
from nox_portal.payments.base import (  # noqa: F401
    Account, CreatedPayment, PaymentEvent, PaymentProvider, ProviderError, WebhookRejected,
)
from nox_portal.payments.mock import MockProvider

log = logging.getLogger("nox_portal.payments")

PROVIDERS: dict[str, type] = {"mock": MockProvider}


def mock_allowed() -> bool:
    s = get_settings()
    return s.email_mode == "log" or s.payment_mock_allowed


def get_provider() -> PaymentProvider | None:
    name = get_settings().payment_provider.strip().lower()
    if name in ("", "none"):
        return None
    if name == "mock" and not mock_allowed():
        return None
    cls = PROVIDERS.get(name)
    return cls() if cls else None


def check_settings() -> None:
    """Startup: an unusable provider setting is logged and online payment stays off (the portal still starts)."""
    name = get_settings().payment_provider.strip().lower()
    if name in ("", "none"):
        return
    if name not in PROVIDERS:
        log.error("NOX_PAYMENT_PROVIDER=%s is unknown (known: %s); online payment is off", name, ", ".join(PROVIDERS))
    elif name == "mock" and not mock_allowed():
        log.error("NOX_PAYMENT_PROVIDER=mock needs NOX_EMAIL_MODE=log or NOX_PAYMENT_MOCK_ALLOWED=true; online payment is off")
    elif name == "mock":
        log.warning("NOX_PAYMENT_PROVIDER=mock: fake checkout, payments are not real. Testing only!")
