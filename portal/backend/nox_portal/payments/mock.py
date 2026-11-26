"""Mock provider for end-to-end tests: the "checkout" is a page of the portal SPA (/pay/mock/<id>) with buttons
«Оплачено» / «Отказ». The page asks the api to sign a webhook payload (HMAC-SHA256 with a key derived from
NOX_SECRET_KEY, never sent to the browser) and posts it to /api/payments/webhook/mock like a real provider would.

Enabled only by NOX_PAYMENT_PROVIDER=mock together with NOX_EMAIL_MODE=log or NOX_PAYMENT_MOCK_ALLOWED=true.
"""

import hashlib
import hmac
import json
import secrets

from starlette.datastructures import Headers

from nox_portal.config import get_settings
from nox_portal.payments.base import Account, CreatedPayment, PaymentEvent, Purpose, WebhookRejected
from nox_portal.security import subkey

SIGNATURE_HEADER = "X-Nox-Mock-Signature"


def sign(body: bytes) -> str:
    return hmac.new(subkey("payments-mock"), body, hashlib.sha256).hexdigest()


def payload(payment_id: str, status: str, amount_kzt: int, token: str | None = None) -> bytes:
    data = {"event_id": "evt_" + secrets.token_hex(8), "payment_id": payment_id, "status": status,
            "amount_kzt": amount_kzt}
    if token:
        data["token"] = token
    return json.dumps(data, separators=(",", ":")).encode()


class MockProvider:
    name = "mock"

    @property
    def supports(self) -> frozenset[str]:
        base = {"card", "kaspi_qr"}
        if get_settings().payment_mock_recurring:
            base.add("recurring")
        return frozenset(base)

    async def create_payment(self, account: Account, purpose: Purpose, amount_kzt: int, description: str,
                             return_url: str, order_id: str) -> CreatedPayment:
        payment_id = f"mock_{order_id}_{secrets.token_hex(6)}"
        base = get_settings().public_url.rstrip("/")
        return CreatedPayment(payment_id=payment_id, redirect_url=f"{base}/pay/mock/{payment_id}")

    async def handle_webhook(self, headers: Headers, body: bytes) -> PaymentEvent:
        got = headers.get(SIGNATURE_HEADER, "")
        if not got or not hmac.compare_digest(got, sign(body)):
            raise WebhookRejected("bad signature")
        try:
            data = json.loads(body)
            status = data["status"]
            if status not in ("succeeded", "failed", "refunded"):
                raise ValueError(status)
            amount = data.get("amount_kzt")
            return PaymentEvent(payment_id=str(data["payment_id"]), status=status,
                                amount_kzt=int(amount) if amount is not None else None,
                                event_id=str(data.get("event_id", ""))[:100],
                                token=data.get("token") if "recurring" in self.supports else None)
        except (ValueError, KeyError, TypeError) as exc:
            raise WebhookRejected("malformed payload") from exc

    async def charge_saved_card(self, token: str, amount_kzt: int, description: str, order_id: str) -> CreatedPayment:
        if "recurring" not in self.supports:
            raise NotImplementedError("mock: recurring is off (NOX_PAYMENT_MOCK_RECURRING)")
        return CreatedPayment(payment_id=f"mock_{order_id}_{secrets.token_hex(6)}")
