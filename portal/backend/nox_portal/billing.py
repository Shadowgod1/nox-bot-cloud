"""Invoices and the payments ledger (payment by bank transfer against an invoice; no online acquiring)."""

import calendar
from dataclasses import asdict
from datetime import datetime

import asyncpg

from nox_portal import tariffs
from nox_portal.config import get_settings
from nox_portal.errors import ApiError
from nox_portal.schemas import Invoice, InvoiceListItem, PaymentOut, Seller, TariffOut


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


# ---- renewal and top-up invoices (table invoices) ---------------------------------------------

INVOICE_COLS = "id, account_id, number, kind, tariff, amount_kzt, coins, period_start, issued_at, paid_at, payment_id"
# an unpaid top-up invoice is reused for this long instead of issuing another one
TOPUP_REUSE = "30 days"


def add_month(value: datetime) -> datetime:
    """Same as Postgres `+ interval '1 month'`: the day is clamped to the length of the next month."""
    year, month = (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


async def next_invoice_number(conn: asyncpg.Connection) -> str:
    seq = await conn.fetchval("SELECT nextval('invoice_seq')")
    return f"NOX-{datetime.now().year}-{seq:05d}"


async def topup_invoice(conn: asyncpg.Connection, account_id: int) -> tuple[asyncpg.Record, bool]:
    """(invoice, created). An unpaid top-up invoice from the last 30 days is returned instead of a new one."""
    open_row = await conn.fetchrow(
        f"SELECT {INVOICE_COLS} FROM invoices WHERE account_id = $1 AND kind = 'topup' AND paid_at IS NULL"
        f" AND issued_at > now() - interval '{TOPUP_REUSE}' ORDER BY issued_at DESC LIMIT 1",
        account_id,
    )
    if open_row is not None:
        return open_row, False
    pack = tariffs.TOPUP
    row = await conn.fetchrow(
        f"INSERT INTO invoices(account_id, number, kind, tariff, amount_kzt, coins) VALUES ($1, $2, 'topup', $3, $4, $5)"
        f" RETURNING {INVOICE_COLS}",
        account_id, await next_invoice_number(conn), pack.code, pack.price_kzt, pack.coins,
    )
    return row, True


async def renewal_invoice(conn: asyncpg.Connection, account_id: int, tariff_code: str, coins: int,
                          period_start: datetime) -> asyncpg.Record | None:
    """The invoice for the period starting at period_start; None when it exists already."""
    tariff = tariffs.get(tariff_code) or tariffs.TARIFFS[0]
    return await conn.fetchrow(
        f"INSERT INTO invoices(account_id, number, kind, tariff, amount_kzt, coins, period_start)"
        f" VALUES ($1, $2, 'renewal', $3, $4, $5, $6) ON CONFLICT DO NOTHING RETURNING {INVOICE_COLS}",
        account_id, await next_invoice_number(conn), tariff.code, tariff.price_kzt, coins, period_start,
    )


def invoice_from_record(account: asyncpg.Record, inv: asyncpg.Record) -> Invoice:
    if inv["kind"] == "topup":
        t = tariffs.TOPUP
        tariff = TariffOut(code=t.code, name=t.name, price_kzt=inv["amount_kzt"], coins=inv["coins"],
                           description=t.description)
    else:
        t = tariffs.get(inv["tariff"]) or tariffs.TARIFFS[0]
        tariff = TariffOut(code=t.code, name=t.name, price_kzt=inv["amount_kzt"], coins=inv["coins"],
                           description=t.description)
    period_end = add_month(inv["period_start"]) if inv["period_start"] is not None else None
    return Invoice(
        number=inv["number"], issued_at=inv["issued_at"], due_days=get_settings().invoice_due_days,
        amount_kzt=inv["amount_kzt"], tariff=tariff, buyer_company=account["company"],
        buyer_contact=account["contact_name"], buyer_email=account["email"], buyer_phone=account["phone"],
        bot_host=f"{account['slug']}.{get_settings().instance_domain}", seller=seller(),
        paid=inv["paid_at"] is not None, kind=inv["kind"], period_start=inv["period_start"], period_end=period_end,
    )


async def invoices_of(conn: asyncpg.Pool | asyncpg.Connection, account_id: int) -> list[InvoiceListItem]:
    """All invoices of an account, newest first; the first (subscription) invoice lives on the account row."""
    out = [
        InvoiceListItem(id=r["id"], number=r["number"], kind=r["kind"], tariff=r["tariff"], amount_kzt=r["amount_kzt"],
                        coins=r["coins"], issued_at=r["issued_at"], period_start=r["period_start"],
                        paid=r["paid_at"] is not None)
        for r in await conn.fetch(f"SELECT {INVOICE_COLS} FROM invoices WHERE account_id = $1"
                                  " ORDER BY issued_at DESC, id DESC", account_id)
    ]
    acc = await conn.fetchrow("SELECT invoice_no, invoice_amount, invoice_issued_at, tariff FROM accounts WHERE id = $1",
                              account_id)
    if acc and acc["invoice_no"]:
        tariff = tariffs.get(acc["tariff"])
        out.append(InvoiceListItem(id=None, number=acc["invoice_no"], kind="subscription", tariff=acc["tariff"],
                                   amount_kzt=acc["invoice_amount"] or 0, coins=tariff.coins if tariff else None,
                                   issued_at=acc["invoice_issued_at"], period_start=None,
                                   paid=await is_paid(conn, account_id, acc["invoice_no"])))
    return out
