"""Online checkout and webhook processing.

  start()        online_payments row (pending) -> provider.create_payment -> redirect URL / QR for the client
  apply_event()  a verified webhook, idempotent by (provider, provider_payment_id):
                   succeeded -> ledger row (method online:<provider>) + exactly what «Отметить оплату» does
                   failed    -> the row is marked failed, the account status is left as it is
                   refunded  -> correcting (negative) ledger row, staff are told (the bot is not stopped)
                 Every verified webhook is logged in payment_events (payload hash, never the payload).
"""

import logging

import asyncpg

from nox_portal import billing, fulfil, letters, mailer, tariffs
from nox_portal.config import get_settings
from nox_portal.deps import audit
from nox_portal.errors import ApiError
from nox_portal.payments import Account, CreatedPayment, PaymentEvent, get_provider

log = logging.getLogger("nox_portal.payments")

PURPOSE_RU = {"subscription": "Подключение", "topup": "Пакет коинов", "renewal": "Продление",
              "upgrade": "Переход на платный тариф"}


def disabled() -> ApiError:
    return ApiError(409, "online_disabled", "Онлайн-оплата пока недоступна: выберите оплату по счёту")


async def start(pool: asyncpg.Pool, account_id: int, *, purpose: str, amount_kzt: int, description: str,
                tariff: str | None = None, coins: int | None = None, invoice_no: str | None = None) -> CreatedPayment:
    provider = get_provider()
    if provider is None:
        raise disabled()
    acc = await pool.fetchrow("SELECT id, email, company, phone FROM accounts WHERE id = $1", account_id)
    op_id = await pool.fetchval(
        "INSERT INTO online_payments(account_id, purpose, amount_kzt, provider, tariff, coins, invoice_no)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
        account_id, purpose, amount_kzt, provider.name, tariff, coins, invoice_no,
    )
    return_url = f"{get_settings().public_url.rstrip('/')}/cabinet?payment={op_id}"
    try:
        created = await provider.create_payment(Account(acc["id"], acc["email"], acc["company"], acc["phone"]),
                                                purpose, amount_kzt, description, return_url, str(op_id))  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - any provider failure: the row is closed, the client may retry
        log.error("payment provider %s: create failed for account %s: %s", provider.name, account_id,
                  type(exc).__name__)
        await pool.execute("UPDATE online_payments SET status = 'failed', updated_at = now() WHERE id = $1", op_id)
        raise ApiError(502, "payment_provider_failed",
                       "Платёжный сервис не отвечает. Повторите позже или выберите оплату по счёту.") from None
    await pool.execute(
        "UPDATE online_payments SET provider_payment_id = $2, redirect_url = $3, updated_at = now() WHERE id = $1",
        op_id, created.payment_id, created.redirect_url,
    )
    await audit(pool, "payment.online_started", account_id=account_id,
                details={"online_payment": op_id, "purpose": purpose, "amount": amount_kzt, "provider": provider.name,
                         "invoice": invoice_no, "tariff": tariff})
    return created


async def _staff_mail(subject_text: tuple[str, str]) -> list[fulfil.Mail]:
    return [(to, subject_text) for to in mailer.staff_recipients()]


async def _fulfil(conn: asyncpg.Connection, op: asyncpg.Record, provider: str) -> tuple[str, int | None, list]:
    """(outcome, ledger row id, e-mails) for a succeeded payment."""
    method = f"online:{provider}"
    ref = op["provider_payment_id"] or ""
    acc = await fulfil.lock_account(conn, op["account_id"])
    what = PURPOSE_RU.get(op["purpose"], op["purpose"])
    amount = tariffs.money(op["amount_kzt"])
    mails: list = []

    async def ledger_only(reason: str) -> tuple[str, int, list]:
        pid = await billing.record_payment(conn, acc["id"], invoice_no=op["invoice_no"], tariff=op["tariff"],
                                           amount_kzt=op["amount_kzt"], reference=ref, note=reason, staff_id=None,
                                           method=method)
        await audit(conn, "payment.online_not_payable", account_id=acc["id"],
                    details={"online_payment": op["id"], "payment": pid, "reason": reason})
        return "not_payable", pid, await _staff_mail(
            letters.staff_online_problem(acc["company"], what, amount, provider, reason))

    if op["purpose"] == "subscription":
        if acc["status"] not in fulfil.PAYABLE or (op["invoice_no"] and op["invoice_no"] != acc["invoice_no"]):
            return await ledger_only(f"заявка в статусе {acc['status']}: оплата записана, бот не создан")
        pid = await fulfil.pay_subscription(conn, acc, amount_kzt=op["amount_kzt"], reference=ref, note="",
                                            staff_id=None, method=method, ip=None, tariff=op["tariff"])
    else:
        if op["invoice_no"]:
            inv = await conn.fetchrow(f"SELECT {billing.INVOICE_COLS} FROM invoices WHERE account_id = $1"
                                      " AND number = $2 FOR UPDATE", acc["id"], op["invoice_no"])
            if inv is None or inv["paid_at"] is not None:
                return await ledger_only(f"счёт {op['invoice_no']} не найден или уже оплачен")
        elif op["purpose"] == "topup":
            inv = await billing.insert_invoice(conn, acc["id"], "topup", tariffs.TOPUP.code, op["amount_kzt"],
                                               op["coins"] or tariffs.TOPUP.coins, note="онлайн-оплата")
        else:
            return await ledger_only("нет счёта для этой оплаты")
        mails += await fulfil.pay_invoice(conn, inv, amount_kzt=op["amount_kzt"], reference=ref, note="",
                                          staff_id=None, method=method, ip=None)
        pid = await conn.fetchval("SELECT payment_id FROM invoices WHERE id = $1", inv["id"])
    mails += await _staff_mail(letters.staff_online_paid(acc["company"], what, amount, provider))
    return "applied", pid, mails


async def apply_event(pool: asyncpg.Pool, provider: str, event: PaymentEvent, payload_sha256: str) -> tuple[str, list]:
    """Returns (outcome, e-mails to send after commit)."""
    mails: list = []
    async with pool.acquire() as conn, conn.transaction():
        op = await conn.fetchrow(
            "SELECT id, account_id, purpose, amount_kzt, provider_payment_id, status, tariff, coins, invoice_no,"
            " payment_id FROM online_payments WHERE provider = $1 AND provider_payment_id = $2 FOR UPDATE",
            provider, event.payment_id,
        )
        if op is None:
            outcome = "unknown_payment"
        elif event.status == "succeeded":
            if op["status"] == "succeeded":
                outcome = "duplicate"
            elif op["status"] == "refunded":
                outcome = "ignored"
            elif event.amount_kzt is not None and event.amount_kzt != op["amount_kzt"]:
                outcome = "amount_mismatch"
                company = await conn.fetchval("SELECT company FROM accounts WHERE id = $1", op["account_id"])
                mails += await _staff_mail(letters.staff_online_problem(
                    company, PURPOSE_RU.get(op["purpose"], op["purpose"]), tariffs.money(event.amount_kzt), provider,
                    f"сумма {tariffs.money(event.amount_kzt)} не совпадает с ожидаемой {tariffs.money(op['amount_kzt'])};"
                    " ничего не начислено, проверьте в кабинете провайдера"))
            else:
                outcome, pid, more = await _fulfil(conn, op, provider)
                mails += more
                await conn.execute(
                    "UPDATE online_payments SET status = 'succeeded', payment_id = $2, last_event_hash = $3,"
                    " updated_at = now() WHERE id = $1", op["id"], pid, payload_sha256)
                if event.token:
                    await conn.execute("UPDATE accounts SET pay_token = $2, pay_token_provider = $3 WHERE id = $1",
                                       op["account_id"], event.token[:500], provider)
        elif event.status == "failed":
            if op["status"] == "pending":
                outcome = "applied"
                await conn.execute("UPDATE online_payments SET status = 'failed', last_event_hash = $2,"
                                   " updated_at = now() WHERE id = $1", op["id"], payload_sha256)
                await audit(conn, "payment.online_failed", account_id=op["account_id"],
                            details={"online_payment": op["id"]})
            else:
                outcome = "duplicate" if op["status"] == "failed" else "ignored"
        else:  # refunded
            if op["status"] == "succeeded":
                outcome = "applied"
                acc = await conn.fetchrow("SELECT company FROM accounts WHERE id = $1", op["account_id"])
                pid = await billing.record_payment(
                    conn, op["account_id"], invoice_no=op["invoice_no"], tariff=op["tariff"],
                    amount_kzt=-op["amount_kzt"], reference=op["provider_payment_id"] or "", note="возврат",
                    staff_id=None, method=f"online:{provider}")
                await conn.execute("UPDATE online_payments SET status = 'refunded', last_event_hash = $2,"
                                   " updated_at = now() WHERE id = $1", op["id"], payload_sha256)
                await audit(conn, "payment.online_refunded", account_id=op["account_id"],
                            details={"online_payment": op["id"], "payment": pid})
                mails += await _staff_mail(letters.staff_online_problem(
                    acc["company"], PURPOSE_RU.get(op["purpose"], op["purpose"]), tariffs.money(op["amount_kzt"]),
                    provider, "провайдер сообщил о возврате; в журнал добавлена строка с минусом, бот не остановлен"))
            else:
                outcome = "duplicate" if op["status"] == "refunded" else "ignored"
        await conn.execute(
            "INSERT INTO payment_events(provider, provider_payment_id, online_payment_id, event_id, status, amount_kzt,"
            " payload_sha256, outcome) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            provider, event.payment_id[:200], op["id"] if op else None, event.event_id, event.status,
            event.amount_kzt, payload_sha256, outcome,
        )
    return outcome, mails
