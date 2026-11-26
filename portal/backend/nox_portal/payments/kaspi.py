"""Kaspi provider (skeleton): the client scans a QR (or opens a deeplink on the phone) that opens the Kaspi app on
our service with the payment code (invoice number digits) and the amount prefilled. Kaspi then calls
/api/payments/kaspi with `check` (does this invoice exist, how much) and `pay` (credit it, Kaspi txn_id).

Invoice-based: there is no checkout session — the client pays an issued invoice, which stays valid for a bank
transfer too. The wire format lives in kaspi_protocol.py (TODO: verify against the official Kaspi document).
"""

import base64
import hashlib
import hmac
import logging
from decimal import Decimal
from ipaddress import ip_address, ip_network

import asyncpg
from starlette.datastructures import Headers

from nox_portal import billing, tariffs
from nox_portal.config import get_settings
from nox_portal.payments import kaspi_protocol as wire
from nox_portal.payments.base import Account, CreatedPayment, PaymentEvent, Purpose, WebhookRejected

log = logging.getLogger("nox_portal.payments.kaspi")


def pay_url(invoice_number: str, amount_kzt: int) -> str:
    template = get_settings().kaspi_pay_url_template
    return (template.replace("{account}", wire.account_code(invoice_number))
            .replace("{amount}", str(amount_kzt)))


class KaspiProvider:
    name = "kaspi"
    supports = frozenset({"kaspi_qr"})
    # pays an issued invoice by its number (QR / deeplink), no checkout session
    invoice_based = True

    def pay_url(self, invoice_number: str, amount_kzt: int) -> str:
        return pay_url(invoice_number, amount_kzt)

    async def create_payment(self, account: Account, purpose: Purpose, amount_kzt: int, description: str,
                             return_url: str, order_id: str) -> CreatedPayment:
        url = pay_url(order_id, amount_kzt)
        return CreatedPayment(payment_id="", redirect_url=url, qr_payload=url)

    async def handle_webhook(self, headers: Headers, body: bytes) -> PaymentEvent:
        raise WebhookRejected("kaspi calls /api/payments/kaspi (check / pay), not the generic webhook")

    async def charge_saved_card(self, token: str, amount_kzt: int, description: str, order_id: str) -> CreatedPayment:
        raise NotImplementedError("kaspi: no recurring payments")


# ---- callback security ----------------------------------------------------------------------


def protection_configured() -> bool:
    s = get_settings()
    return bool(s.kaspi_allowed_ips.strip() or s.kaspi_basic_auth.strip())


def ip_allowed(ip: str) -> bool:
    raw = get_settings().kaspi_allowed_ips.strip()
    if not raw:
        return True
    try:
        addr = ip_address(ip)
    except ValueError:
        return False
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if addr in ip_network(part, strict=False):
                return True
        except ValueError:
            log.error("NOX_KASPI_ALLOWED_IPS: bad entry %r", part)
    return False


def auth_ok(header: str | None) -> bool:
    expected = get_settings().kaspi_basic_auth.strip()
    if not expected:
        return True
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        got = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(hashlib.sha256(got.encode()).digest(), hashlib.sha256(expected.encode()).digest())


# ---- check / pay ----------------------------------------------------------------------------


async def find_invoice(conn: asyncpg.Pool | asyncpg.Connection, code: str) -> dict | None:
    """The open invoice behind a payment code: {account_id, number, amount, purpose, tariff, coins, payable}."""
    acc = await conn.fetchrow(
        "SELECT id, status, tariff, invoice_no, invoice_amount FROM accounts"
        " WHERE regexp_replace(invoice_no, '[^0-9]', '', 'g') = $1 AND invoice_amount IS NOT NULL", code)
    if acc is not None:
        paid = await billing.is_paid(conn, acc["id"], acc["invoice_no"])
        t = tariffs.get(acc["tariff"])
        return {"account_id": acc["id"], "number": acc["invoice_no"], "amount": acc["invoice_amount"],
                "purpose": "subscription", "tariff": acc["tariff"], "coins": t.coins if t else None,
                "payable": not paid and acc["status"] in ("awaiting_payment", "payment_pending")}
    inv = await conn.fetchrow(
        "SELECT i.account_id, i.number, i.kind, i.tariff, i.amount_kzt, i.coins, i.paid_at, a.status"
        " FROM invoices i JOIN accounts a ON a.id = i.account_id"
        " WHERE regexp_replace(i.number, '[^0-9]', '', 'g') = $1", code)
    if inv is None:
        return None
    return {"account_id": inv["account_id"], "number": inv["number"], "amount": inv["amount_kzt"],
            "purpose": inv["kind"], "tariff": inv["tariff"], "coins": inv["coins"],
            "payable": inv["paid_at"] is None and inv["status"] not in ("removed", "rejected")}


def sum_problem(sent: Decimal | None, due: int) -> int | None:
    """Exact amount only (no partial payments)."""
    if sent is None or sent == Decimal(due):
        return None
    return wire.SUM_TOO_SMALL if sent < due else wire.SUM_TOO_LARGE


async def record_pay(pool: asyncpg.Pool, req: wire.KaspiRequest, inv: dict) -> tuple[int, bool]:
    """Create (or find) the online_payments row of this Kaspi txn_id. Returns (row id, created)."""
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchval(
            "SELECT id FROM online_payments WHERE provider = 'kaspi' AND provider_payment_id = $1", req.txn_id)
        if existing is not None:
            return existing, False
        try:
            async with conn.transaction():
                op_id = await conn.fetchval(
                    "INSERT INTO online_payments(account_id, purpose, amount_kzt, provider, provider_payment_id,"
                    " tariff, coins, invoice_no) VALUES ($1, $2, $3, 'kaspi', $4, $5, $6, $7) RETURNING id",
                    inv["account_id"], inv["purpose"], inv["amount"], req.txn_id, inv["tariff"], inv["coins"],
                    inv["number"])
        except asyncpg.UniqueViolationError:
            return await conn.fetchval("SELECT id FROM online_payments WHERE provider = 'kaspi'"
                                       " AND provider_payment_id = $1", req.txn_id), False
    return op_id, True

