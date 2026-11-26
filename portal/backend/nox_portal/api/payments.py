"""Online payments: provider webhooks (public, signature-verified, idempotent, rate-limited) and the mock checkout."""

import hashlib
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from nox_portal import mailer, payments, ratelimit
from nox_portal.db import get_pool
from nox_portal.deps import Principal, audit, csrf_protect, current_client
from nox_portal.errors import ApiError
from nox_portal.payments import kaspi
from nox_portal.payments import kaspi_protocol as wire
from nox_portal.payments import mock as mock_provider
from nox_portal.payments import service
from nox_portal.payments.base import PaymentEvent
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


# ---- Kaspi check / pay (NOX_PAYMENT_PROVIDER=kaspi) ---------------------------------------------


@router.api_route("/api/payments/kaspi", methods=["GET", "POST"])
async def kaspi_callback(request: Request):
    """Kaspi "Платежи" service-provider callback: check (is this invoice open, how much) and pay (credit it).
    Accepted only from NOX_KASPI_ALLOWED_IPS and/or with NOX_KASPI_BASIC_AUTH; pay is idempotent by txn_id and
    needs the exact invoice amount. Wire format: payments/kaspi_protocol.py (unverified, see the TODO there)."""
    ip = ratelimit.client_ip(request)
    await ratelimit.hit("kaspi:ip", ip)
    prov = payments.get_provider()
    if prov is None or prov.name != "kaspi":
        raise ApiError(404, "not_found", "Not found")
    pool = get_pool()
    if not kaspi.protection_configured() or not kaspi.ip_allowed(ip) or not kaspi.auth_ok(
            request.headers.get("authorization")):
        log.warning("kaspi callback from %s refused (allowlist / auth)", ip)
        await audit(pool, "payment.kaspi_refused", ip=ip)
        return JSONResponse({"result": wire.OTHER_ERROR, "comment": "forbidden"}, status_code=403)
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise ApiError(413, "too_large", "payload too large")
    query = dict(request.query_params)
    try:
        req = wire.parse(query, body, request.headers.get("content-type", ""))
    except wire.BadRequest as exc:
        return wire.response(query.get("txn_id", "")[:64], wire.BAD_FORMAT, f"неверный параметр: {exc}")
    try:
        if req.command == "check":
            inv = await kaspi.find_invoice(pool, req.account)
            if inv is None or not inv["payable"]:
                return wire.response(req.txn_id, wire.ACCOUNT_NOT_FOUND, "счёт не найден или уже оплачен")
            problem = kaspi.sum_problem(req.sum, inv["amount"])
            if problem is not None:
                return wire.response(req.txn_id, problem, f"сумма к оплате {inv['amount']} ₸", amount=inv["amount"])
            return wire.response(req.txn_id, wire.OK, f"Nox Bot, счёт № {inv['number']}", amount=inv["amount"])
        if req.command == "pay":
            existing = await pool.fetchrow("SELECT id, status FROM online_payments WHERE provider = 'kaspi'"
                                           " AND provider_payment_id = $1", req.txn_id)
            if existing is not None and existing["status"] == "succeeded":
                return wire.response(req.txn_id, wire.OK, "уже зачислено", prv_txn=str(existing["id"]))
            if req.sum is None:
                return wire.response(req.txn_id, wire.BAD_FORMAT, "нет суммы")
            if existing is None:
                inv = await kaspi.find_invoice(pool, req.account)
                if inv is None or not inv["payable"]:
                    return wire.response(req.txn_id, wire.ACCOUNT_NOT_FOUND, "счёт не найден или уже оплачен")
                problem = kaspi.sum_problem(req.sum, inv["amount"])
                if problem is not None:
                    return wire.response(req.txn_id, problem, f"сумма к оплате {inv['amount']} ₸",
                                         amount=inv["amount"])
                op_id, _ = await kaspi.record_pay(pool, req, inv)
            else:
                op_id = existing["id"]  # an earlier attempt of this txn stopped half-way: finish it
            digest = hashlib.sha256(
                "&".join(f"{k}={v}" for k, v in sorted({**query, "_body": body.decode("utf-8", "replace")}.items()))
                .encode()).hexdigest()
            outcome, mails = await service.apply_event(
                pool, "kaspi", PaymentEvent(payment_id=req.txn_id, status="succeeded", amount_kzt=int(req.sum),
                                            event_id=req.txn_id), digest)
            for to, mail in mails:
                await mailer.send_quiet(to, *mail)
            if outcome == "amount_mismatch":
                return wire.response(req.txn_id, wire.SUM_TOO_SMALL, "сумма не совпадает со счётом")
            comment = "зачислено" if outcome in ("applied", "duplicate") else "зачислено, проверит менеджер"
            return wire.response(req.txn_id, wire.OK, comment, prv_txn=str(op_id))
        return wire.response(req.txn_id, wire.OTHER_ERROR, "неизвестная команда")
    except Exception:  # noqa: BLE001 - Kaspi retries on a temporary error
        log.exception("kaspi %s %s failed", req.command, req.txn_id)
        return wire.response(req.txn_id, wire.TEMPORARY_ERROR, "временная ошибка, повторите")


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
