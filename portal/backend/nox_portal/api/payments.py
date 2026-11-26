"""Online payments: provider webhooks (public, signature-verified, idempotent, rate-limited) and the mock checkout."""

import hashlib
import logging

from fastapi import APIRouter, Depends, Request

from nox_portal import mailer, payments, ratelimit
from nox_portal.db import get_pool
from nox_portal.deps import Principal, audit, csrf_protect, current_client
from nox_portal.errors import ApiError
from nox_portal.payments import mock as mock_provider
from nox_portal.payments import service
from nox_portal.schemas import MockResult

log = logging.getLogger("nox_portal.payments")
router = APIRouter(tags=["payments"])

MAX_WEBHOOK_BYTES = 64 * 1024


@router.post("/api/payments/webhook/{provider}")
async def post_webhook(provider: str, request: Request):
    """A provider reports a payment result. 401 for a bad signature (nothing changes), 200 otherwise — also for
    duplicates and unknown payments, so the provider stops retrying."""
    ip = ratelimit.client_ip(request)
    await ratelimit.hit("payhook:ip", ip)
    prov = payments.get_provider()
    if prov is None or prov.name != provider:
        raise ApiError(404, "not_found", "Not found")
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise ApiError(413, "too_large", "payload too large")
    try:
        event = await prov.handle_webhook(request.headers, body)
    except payments.WebhookRejected as exc:
        log.warning("payment webhook %s from %s rejected: %s", provider, ip, exc)
        await audit(get_pool(), "payment.webhook_rejected", ip=ip, details={"provider": provider, "reason": str(exc)})
        raise ApiError(401, "bad_signature", "signature check failed") from None
    outcome, mails = await service.apply_event(get_pool(), prov.name, event, hashlib.sha256(body).hexdigest())
    for to, mail in mails:
        await mailer.send_quiet(to, *mail)
    return {"ok": True, "outcome": outcome}


# ---- mock checkout (NOX_PAYMENT_PROVIDER=mock only) ---------------------------------------------


async def _mock_payment(payment_id: str, account_id: int):
    prov = payments.get_provider()
    if prov is None or prov.name != "mock":
        raise ApiError(404, "not_found", "Not found")
    row = await get_pool().fetchrow(
        "SELECT id, purpose, amount_kzt, status, invoice_no, tariff FROM online_payments"
        " WHERE provider = 'mock' AND provider_payment_id = $1 AND account_id = $2", payment_id, account_id)
    if row is None:
        raise ApiError(404, "not_found", "Платёж не найден")
    return row


@router.get("/api/payments/mock/{payment_id}")
async def get_mock_payment(payment_id: str, client: Principal = Depends(current_client)):
    row = await _mock_payment(payment_id, client.id)
    return {"payment_id": payment_id, "purpose": row["purpose"], "purpose_ru": service.PURPOSE_RU[row["purpose"]],
            "amount_kzt": row["amount_kzt"], "status": row["status"], "invoice_no": row["invoice_no"],
            "return_url": f"/cabinet?payment={row['id']}"}


@router.post("/api/payments/mock/{payment_id}/sign", dependencies=[Depends(current_client), Depends(csrf_protect)])
async def post_mock_sign(payment_id: str, body: MockResult, client: Principal = Depends(current_client)):
    """Signed webhook payload for the fake checkout page; the page posts it to the public webhook itself."""
    row = await _mock_payment(payment_id, client.id)
    token = "mocktok_" + payment_id[-12:] if body.result == "succeeded" and \
        "recurring" in mock_provider.MockProvider().supports else None
    raw = mock_provider.payload(payment_id, body.result, row["amount_kzt"], token)
    return {"webhook_url": "/api/payments/webhook/mock", "payload": raw.decode(),
            "signature_header": mock_provider.SIGNATURE_HEADER, "signature": mock_provider.sign(raw)}
