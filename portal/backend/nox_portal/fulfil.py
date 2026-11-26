"""What happens when something is paid or approved — shared by staff actions and online-payment webhooks.

  approve()            queue the create job, issue the gateway key, start the first period (Free: trial end)
  pay_subscription()   ledger row + approve (the first payment of a new application)
  pay_invoice()        ledger row + effect of a renewal / top-up / upgrade invoice
  apply_upgrade()      a Free account moves to a paid tariff: paid period with the same bot, trial suspension lifted

Every function expects the caller's transaction; e-mails are returned (or sent by the caller) after commit.
"""

import logging

import asyncpg

from nox_portal import billing, jobs, letters, renewal, tariffs, wallet
from nox_portal.config import get_settings
from nox_portal.deps import audit
from nox_portal.errors import ApiError

log = logging.getLogger("nox_portal.fulfil")

# a new application can be paid (or approved) in these statuses
PAYABLE = ("verified", "awaiting_invoice", "payment_pending", "awaiting_payment")
# a Free account with a bot (or one on the way) can move to a paid tariff
UPGRADABLE = ("approved", "provisioning", "active", "suspended")

Mail = tuple[str, tuple[str, str]]


async def lock_account(conn: asyncpg.Connection, account_id: int) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, email, contact_name, company, phone, slug, status, coins_limit, tariff, invoice_no, invoice_amount,"
        " local_install, suspend_reason, free_until, period_start, period_end, upgrade_tariff FROM accounts"
        " WHERE id = $1 FOR UPDATE",
        account_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "Заявка не найдена")
    return row


async def approve(conn: asyncpg.Connection, row: asyncpg.Record, *, coins: int, sealed: str | None = None,
                  slug: str | None = None, tariff: str | None = None, staff_id: int | None, ip: str | None,
                  payment_id: int | None, action: str = "staff.approve") -> int:
    """Queue the create job (the caller holds the row lock). Returns the job id.

    Without a per-client Anthropic key the account gets an LLM gateway virtual key (sealed into the job for the
    provisioner) and its first billing period starts now with `coins` monthly coins. With a per-client key the
    bot talks to Anthropic directly and the gateway is not used. A Free account gets its trial end date."""
    account_id = row["id"]
    new_slug = row["slug"]
    if slug and slug != new_slug:
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM accounts WHERE slug = $1)", slug):
            raise ApiError(409, "slug_taken", "Этот адрес уже занят")
        new_slug = slug
    code = tariff or row["tariff"]
    free = tariffs.is_free(code)
    await conn.execute(
        "UPDATE accounts SET status = 'approved', slug = $2, coins_limit = $3, tariff = $4, reject_reason = NULL,"
        " last_error = NULL, suspend_reason = NULL, upgrade_tariff = NULL,"
        " free_until = CASE WHEN $5 THEN now() + make_interval(days => $6) ELSE NULL END,"
        " pay_method = CASE WHEN $5 THEN 'free' ELSE pay_method END, updated_at = now() WHERE id = $1",
        account_id, new_slug, coins, code, free, get_settings().free_days,
    )
    gateway_sealed, key_id = None, None
    if sealed is None:
        await wallet.revoke_keys(conn, account_id, "superseded by a new approval")
        key, key_id = await wallet.issue_key(conn, account_id, staff_id)
        gateway_sealed = wallet.seal_key(key)
        del key
        await wallet.start_period(conn, account_id, coins, staff_id=staff_id,
                                  note="free trial" if free else "first period")
    job_id = await jobs.enqueue(conn, account_id, "create", new_slug, coins=coins, llm_key_sealed=sealed,
                                staff_id=staff_id, gateway_key_sealed=gateway_sealed, key_id=key_id)
    await audit(conn, action, staff_id=staff_id, account_id=account_id, ip=ip,
                details={"job": job_id, "coins": coins, "slug": new_slug, "slug_changed": new_slug != row["slug"],
                         "custom_key": sealed is not None, "payment": payment_id, "gateway_key": key_id,
                         "tariff": code})
    return job_id


async def pay_subscription(conn: asyncpg.Connection, row: asyncpg.Record, *, amount_kzt: int, reference: str,
                           note: str, staff_id: int | None, method: str, ip: str | None, tariff: str | None = None,
                           coins: int | None = None, slug: str | None = None, sealed: str | None = None) -> int:
    """Record the first payment and approve (the caller holds the row lock and has checked the status).
    The payment is booked against the subscription invoice; an online payment without one gets a number now,
    so the cabinet shows a paid invoice (receipt). Returns the ledger row id."""
    code = tariff or row["tariff"]
    t = tariffs.get(code)
    coins = coins if coins is not None else (t.coins if t else None)
    if coins is None:
        raise ApiError(422, "validation_error", "coins: укажите лимит коинов")
    number = row["invoice_no"]
    if number is None or row["invoice_amount"] is None:
        number = number or await billing.next_invoice_number(conn)
        await conn.execute(
            "UPDATE accounts SET invoice_no = $2, invoice_amount = $3, invoice_issued_at = now(), tariff = $4"
            " WHERE id = $1", row["id"], number, amount_kzt, code)
    payment_id = await billing.record_payment(conn, row["id"], invoice_no=number, tariff=code, amount_kzt=amount_kzt,
                                              reference=reference, note=note, staff_id=staff_id, method=method)
    await audit(conn, "staff.mark_paid" if staff_id else "payment.online_paid", staff_id=staff_id,
                account_id=row["id"], ip=ip,
                details={"payment": payment_id, "invoice": number, "amount": amount_kzt, "method": method,
                         "invoice_amount": row["invoice_amount"], "reference": reference.strip()})
    await approve(conn, row, coins=coins, sealed=sealed, slug=slug, tariff=code, staff_id=staff_id, ip=ip,
                  payment_id=payment_id)
    return payment_id


async def apply_upgrade(conn: asyncpg.Connection, account_id: int, tariff_code: str, coins: int,
                        invoice_no: str) -> dict:
    """Free -> paid tariff with the same bot: a paid period starts now with the tariff's coins (trial coins
    expire), the trial end is cleared and a trial suspension is lifted (resume job for a cloud bot)."""
    acc = await lock_account(conn, account_id)
    await conn.execute(
        "UPDATE accounts SET tariff = $2, coins_limit = $3, free_until = NULL, upgrade_tariff = NULL,"
        " updated_at = now() WHERE id = $1", account_id, tariff_code, coins)
    result: dict = {"tariff": tariff_code, "coins": coins, "invoice": invoice_no}
    if acc["period_end"] is not None:
        await wallet.start_period(conn, account_id, coins, staff_id=None, note=f"upgrade {invoice_no}")
    if acc["status"] == "suspended" and acc["suspend_reason"] == "trial_ended":
        if acc["local_install"]:
            await conn.execute("UPDATE accounts SET status = 'active', suspend_reason = NULL, updated_at = now()"
                               " WHERE id = $1", account_id)
        else:
            await conn.execute("UPDATE accounts SET suspend_reason = NULL WHERE id = $1", account_id)
            try:
                result["resume_job"] = await jobs.enqueue(conn, account_id, "resume", acc["slug"])
            except ApiError:
                log.warning("account %s: upgrade paid but another job is open; resume it by hand", account_id)
    await audit(conn, "billing.upgraded", account_id=account_id, details=result)
    return result


async def pay_invoice(conn: asyncpg.Connection, inv: asyncpg.Record, *, amount_kzt: int, reference: str, note: str,
                      staff_id: int | None, method: str, ip: str | None) -> list[Mail]:
    """Payment of a renewal / top-up / upgrade invoice (the caller holds the invoice row lock and has checked it
    is unpaid). Top-up: coins at once. Renewal: the period rolls over when it has ended (an account suspended
    for non-payment is renewed and resumed). Upgrade: apply_upgrade. Returns the e-mails to send."""
    account = await conn.fetchrow("SELECT id, email, contact_name FROM accounts WHERE id = $1 FOR UPDATE",
                                  inv["account_id"])
    payment_id = await billing.record_payment(
        conn, inv["account_id"], invoice_no=inv["number"], tariff=inv["tariff"], amount_kzt=amount_kzt,
        reference=reference, note=note, staff_id=staff_id, method=method,
    )
    await conn.execute("UPDATE invoices SET paid_at = now(), payment_id = $2 WHERE id = $1", inv["id"], payment_id)
    details = {"invoice": inv["number"], "kind": inv["kind"], "payment": payment_id, "amount": amount_kzt,
               "invoice_amount": inv["amount_kzt"], "reference": reference.strip(), "method": method}
    mails: list[Mail] = []
    if inv["kind"] == "topup":
        await conn.execute(
            "INSERT INTO coin_grants(account_id, kind, coins, invoice_no, payment_id, staff_id, note)"
            " VALUES ($1, 'topup', $2, $3, $4, $5, $6)",
            inv["account_id"], inv["coins"], inv["number"], payment_id, staff_id, note.strip(),
        )
        mails.append((account["email"], letters.coins_added(account["contact_name"], inv["coins"])))
    elif inv["kind"] == "upgrade":
        details["upgrade"] = await apply_upgrade(conn, inv["account_id"], inv["tariff"], inv["coins"], inv["number"])
        t = tariffs.get(inv["tariff"])
        mails.append((account["email"], letters.upgraded(account["contact_name"], t.name if t else inv["tariff"])))
    else:
        details["renewal"] = await renewal.renew_if_due(conn, inv["account_id"])
    await audit(conn, "staff.invoice_paid" if staff_id else "payment.online_invoice_paid", staff_id=staff_id,
                account_id=inv["account_id"], ip=ip, details=details)
    return mails
