"""Online payment provider interface. A provider never hands card data to the portal: the client pays on the
provider's page (or by Kaspi QR), the provider reports the result with a signed webhook.

A real provider (chosen later) implements PaymentProvider in its own module and is added to registry.PROVIDERS.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from starlette.datastructures import Headers

Purpose = Literal["subscription", "topup", "renewal", "upgrade"]
EventStatus = Literal["succeeded", "failed", "refunded"]
# what a provider can do; the cabinet shows the matching buttons
CAPABILITIES = frozenset({"card", "kaspi_qr", "recurring", "apple_google_pay"})


@dataclass(frozen=True)
class Account:
    id: int
    email: str
    company: str
    phone: str


@dataclass(frozen=True)
class CreatedPayment:
    payment_id: str
    # one of the two: the provider's checkout page, or a QR payload shown by the portal
    redirect_url: str | None = None
    qr_payload: str | None = None


@dataclass(frozen=True)
class PaymentEvent:
    """A verified webhook. amount_kzt None = the provider does not report it (then it is not checked)."""
    payment_id: str
    status: EventStatus
    amount_kzt: int | None = None
    event_id: str = ""
    # opaque recurring token (only providers with "recurring"); never a card number
    token: str | None = None


class WebhookRejected(Exception):
    """Bad or missing signature, malformed payload: answered 401, nothing is changed."""


class ProviderError(Exception):
    """The provider could not create the payment (network, its API refused)."""


class PaymentProvider(Protocol):
    name: str
    supports: frozenset[str]
    # True: the client pays an issued invoice by its number (QR / deeplink, e.g. Kaspi), no checkout session;
    # the provider calls back with its own protocol. False: create_payment opens a checkout + signed webhook.
    invoice_based: bool

    async def create_payment(self, account: Account, purpose: Purpose, amount_kzt: int, description: str,
                             return_url: str, order_id: str) -> CreatedPayment: ...

    async def handle_webhook(self, headers: Headers, body: bytes) -> PaymentEvent:
        """Verify the signature over the raw body and parse it; raise WebhookRejected otherwise."""
        ...

    async def charge_saved_card(self, token: str, amount_kzt: int, description: str, order_id: str) -> CreatedPayment:
        """Only when "recurring" in supports; the result arrives by webhook like any payment."""
        ...
