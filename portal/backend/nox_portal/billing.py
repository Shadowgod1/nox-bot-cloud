"""Invoices and the payments ledger (payment by bank transfer against an invoice; no online acquiring)."""

from dataclasses import asdict
from datetime import datetime

import asyncpg

from nox_portal import tariffs
from nox_portal.config import get_settings
from nox_portal.errors import ApiError
from nox_portal.schemas import Invoice, PaymentOut, Seller, TariffOut


def seller() -> Seller:
    s = get_settings()
    return Seller(name=s.seller_name, bin=s.seller_bin, address=s.seller_address, bank=s.seller_bank,
                  iik=s.seller_iik, bik=s.seller_bik, kbe=s.seller_kbe, phone=s.seller_phone)


async def choose_tariff(conn: asyncpg.Connection, account_id: int, code: str) -> asyncpg.Record:
    """verified -> awaiting_payment with an invoice; while unpaid the client may switch tariff (same invoice
    number, new amount). Returns the updated account row."""
    tariff = tariffs.get(code)
    if tariff is None:
        raise ApiError(422, "validation_error", "tariff: неизвестный тариф")
    row = await conn.fetchrow("SELECT id, status, invoice_no FROM accounts WHERE id = $1 FOR UPDATE", account_id)
    if row["status"] not in ("verified", "awaiting_payment"):
        raise ApiError(409, "wrong_status", "Тариф можно выбрать только до оплаты счёта")
    number = row["invoice_no"]
    if number is None:
        seq = await conn.fetchval("SELECT nextval('invoice_seq')")
        number = f"NOX-{datetime.now().year}-{seq:05d}"
    return await conn.fetchrow(
        "UPDATE accounts SET status = 'awaiting_payment', tariff = $2, invoice_no = $3, invoice_amount = $4,"
        " invoice_issued_at = now(), updated_at = now() WHERE id = $1"
        " RETURNING id, email, contact_name, company, phone, slug, tariff, invoice_no, invoice_amount, invoice_issued_at",
        account_id, tariff.code, number, tariff.price_kzt,
    )


def invoice_from_row(row: asyncpg.Record, paid: bool) -> Invoice:
    tariff = tariffs.get(row["tariff"])
    if tariff is None or row["invoice_no"] is None:
        raise ApiError(404, "no_invoice", "Счёт ещё не выставлен: выберите тариф")
    return Invoice(
        number=row["invoice_no"], issued_at=row["invoice_issued_at"], due_days=get_settings().invoice_due_days,
        amount_kzt=row["invoice_amount"], tariff=TariffOut(**asdict(tariff)),
        buyer_company=row["company"], buyer_contact=row["contact_name"], buyer_email=row["email"],
        buyer_phone=row["phone"], bot_host=f"{row['slug']}.{get_settings().instance_domain}", seller=seller(),
        paid=paid,
    )


async def is_paid(conn: asyncpg.Pool | asyncpg.Connection, account_id: int, invoice_no: str | None) -> bool:
    if not invoice_no:
        return False
    total = await conn.fetchval(
        "SELECT coalesce(sum(amount_kzt), 0) FROM payments WHERE account_id = $1 AND invoice_no = $2",
        account_id, invoice_no,
    )
    return total > 0


async def record_payment(conn: asyncpg.Connection, account_id: int, *, invoice_no: str | None, tariff: str | None,
                         amount_kzt: int, reference: str, note: str, staff_id: int) -> int:
    return await conn.fetchval(
        "INSERT INTO payments(account_id, invoice_no, tariff, amount_kzt, reference, note, staff_id)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
        account_id, invoice_no, tariff, amount_kzt, reference.strip(), note.strip(), staff_id,
    )


async def payments_of(conn: asyncpg.Pool | asyncpg.Connection, account_id: int) -> list[PaymentOut]:
    rows = await conn.fetch(
        "SELECT p.id, p.invoice_no, p.tariff, p.amount_kzt, p.method, p.reference, p.note, s.email AS staff_email,"
        " p.created_at FROM payments p LEFT JOIN staff s ON s.id = p.staff_id WHERE p.account_id = $1"
        " ORDER BY p.created_at DESC, p.id DESC",
        account_id,
    )
    return [PaymentOut(**dict(r)) for r in rows]
