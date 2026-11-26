"""Nox staff: review applications, approve / reject / retry / suspend / resume / remove, audit log.
Every endpoint here requires a staff session; mutations also require the CSRF header."""

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response

from dataclasses import asdict

from nox_portal import billing, jobs, letters, mailer, ratelimit, renewal, security, tariffs, wallet
from nox_portal.config import get_settings
from nox_portal.db import get_pool
from nox_portal.deps import (
    Principal, audit, clear_session_cookies, csrf_protect, current_staff, is_locked, register_failure,
    register_success, set_session_cookies,
)
from nox_portal.errors import ApiError
from nox_portal.schemas import (
    ApplicationDetail, ApplicationListItem, Approve, AuditOut, Credentials, Invoice, JobOut, KeyIssued, KeyOut,
    LlmBalance, LocalAccount, LocalAccountCreated, MarkInvoicePaid, MarkPaid, Ok, Reject, RemoveConfirm, StaffConfig,
    StaffMe, Status, TariffOut,
)

router = APIRouter(tags=["staff"])
staff_only = [Depends(current_staff)]
# auth first (401 for anonymous), then CSRF (403); FastAPI caches current_staff per request
staff_mutation = [Depends(current_staff), Depends(csrf_protect)]

COIN_PRESETS = [30000, 60000, 120000]
_LIST_COLS = "id, company, contact_name, email, phone, slug, status, coins_limit, tariff, created_at, updated_at"


# ---- auth -----------------------------------------------------------------------------------


@router.post("/api/staff/auth/login", response_model=StaffMe)
async def post_staff_login(body: Credentials, request: Request, response: Response):
    ip = ratelimit.client_ip(request)
    await ratelimit.hit("staff-login:ip", ip)
    await ratelimit.hit("staff-login:email", body.email)
    pool = get_pool()
    row = await pool.fetchrow("SELECT id, email, name, password_hash, disabled FROM staff WHERE email = $1", body.email)
    bad = ApiError(401, "invalid_credentials", "Неверный e-mail или пароль")
    if row is None:
        await security.verify_password(None, body.password)
        raise bad
    if row["disabled"] or await is_locked(pool, "staff", row["id"]):
        await security.verify_password(row["password_hash"], body.password)
        raise bad
    if not await security.verify_password(row["password_hash"], body.password):
        if await register_failure(pool, "staff", row["id"]):
            await audit(pool, "staff.locked", staff_id=row["id"], ip=ip)
        raise bad
    await register_success(pool, "staff", row["id"])
    await audit(pool, "staff.login", staff_id=row["id"], ip=ip)
    set_session_cookies(response, "staff", row["id"], row["password_hash"])
    return StaffMe(id=row["id"], email=row["email"], name=row["name"])


@router.post("/api/staff/auth/logout", response_model=Ok)
async def post_staff_logout(response: Response):
    clear_session_cookies(response)
    return Ok()


@router.get("/api/staff/auth/me", response_model=StaffMe)
async def get_staff_me(staff: Principal = Depends(current_staff)):
    row = await get_pool().fetchrow("SELECT id, email, name FROM staff WHERE id = $1", staff.id)
    return StaffMe(id=row["id"], email=row["email"], name=row["name"])


@router.get("/api/staff/config", response_model=StaffConfig, dependencies=staff_only)
async def get_staff_config():
    try:
        security.load_provisioner_pubkey()
        sealing = True
    except security.SealingUnavailable:
        sealing = False
    return StaffConfig(tariffs=tariffs.as_dicts(), coin_presets=COIN_PRESETS, instance_domain=get_settings().instance_domain,
                       custom_key_supported=sealing, topup=TariffOut(**asdict(tariffs.TOPUP)),
                       gateway_url=get_settings().public_url.rstrip("/"))


# ---- applications ---------------------------------------------------------------------------


@router.get("/api/staff/applications", response_model=list[ApplicationListItem], dependencies=staff_only)
async def get_applications(status: Status | None = None, q: str | None = Query(None, max_length=100),
                           limit: int = Query(200, ge=1, le=500)):
    where, args = [], []
    if status:
        args.append(status)
        where.append(f"status = ${len(args)}")
    if q:
        args.append(f"%{q.strip().lower()}%")
        n = len(args)
        where.append(f"(lower(company) LIKE ${n} OR email LIKE ${n} OR slug LIKE ${n} OR lower(contact_name) LIKE ${n})")
    args.append(limit)
    sql = (f"SELECT {_LIST_COLS} FROM accounts {'WHERE ' + ' AND '.join(where) if where else ''}"
           f" ORDER BY created_at DESC LIMIT ${len(args)}")
    return [ApplicationListItem(**dict(r)) for r in await get_pool().fetch(sql, *args)]


@router.get("/api/staff/counts", dependencies=staff_only)
async def get_counts():
    rows = await get_pool().fetch("SELECT status, count(*) AS n FROM accounts GROUP BY status")
    return {r["status"]: r["n"] for r in rows}


def _job_out(r: asyncpg.Record) -> JobOut:
    return JobOut(id=r["id"], kind=r["kind"], status=r["status"], slug=r["slug"], coins=r["coins"],
                  has_custom_key=r["has_custom_key"], has_gateway_key=r["has_gateway_key"], error=r["error"], created_at=r["created_at"],
                  claimed_at=r["claimed_at"], finished_at=r["finished_at"])


async def _audit_rows(conn, account_id: int | None, limit: int) -> list[AuditOut]:
    rows = await conn.fetch(
        "SELECT l.id, l.action, s.email AS staff_email, l.account_id, a.company, l.details, l.ip, l.created_at"
        " FROM audit_log l LEFT JOIN staff s ON s.id = l.staff_id LEFT JOIN accounts a ON a.id = l.account_id"
        " WHERE ($1::bigint IS NULL OR l.account_id = $1) ORDER BY l.created_at DESC, l.id DESC LIMIT $2",
        account_id, limit,
    )
    return [AuditOut(**dict(r)) for r in rows]


@router.get("/api/staff/applications/{account_id}", response_model=ApplicationDetail, dependencies=staff_only)
async def get_application(account_id: int):
    pool = get_pool()
    row = await pool.fetchrow(
        f"SELECT {_LIST_COLS}, admin_url, port, reject_reason, last_error, email_verified_at, register_ip,"
        " invoice_no, invoice_amount, invoice_issued_at, local_install, period_start"
        " FROM accounts WHERE id = $1",
        account_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "Заявка не найдена")
    job_rows = await pool.fetch(
        "SELECT id, kind, status, slug, coins, llm_key_sealed IS NOT NULL AS has_custom_key,"
        " gateway_key_sealed IS NOT NULL AS has_gateway_key, error, created_at,"
        " claimed_at, finished_at FROM jobs WHERE account_id = $1 ORDER BY created_at DESC, id DESC LIMIT 50",
        account_id,
    )
    keys = [KeyOut(**dict(k)) for k in await pool.fetch(
        "SELECT id, prefix, status, created_at, last_used_at, revoked_at FROM virtual_keys WHERE account_id = $1"
        " ORDER BY created_at DESC, id DESC LIMIT 20", account_id)]
    data = dict(row)
    data.pop("period_start")
    return ApplicationDetail(
        **data, bot_host=f"{row['slug']}.{get_settings().instance_domain}",
        llm=await llm_balance(pool, account_id) if row["period_start"] else None, keys=keys,
        invoices=await billing.invoices_of(pool, account_id),
        payments=await billing.payments_of(pool, account_id),
        jobs=[_job_out(r) for r in job_rows], audit=await _audit_rows(pool, account_id, 100),
    )


async def _lock_account(conn, account_id: int) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, email, contact_name, slug, status, coins_limit, tariff, invoice_no, invoice_amount"
        " FROM accounts WHERE id = $1 FOR UPDATE",
        account_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "Заявка не найдена")
    return row


def _require_status(row: asyncpg.Record, *allowed: str) -> None:
    if row["status"] not in allowed:
        raise ApiError(409, "wrong_status", f"Действие недоступно в статусе «{row['status']}»")


def _seal(key: str | None) -> str | None:
    if not key:
        return None
    try:
        return security.seal_for_provisioner(key)
    except security.SealingUnavailable:
        raise ApiError(503, "custom_key_unavailable",
                       "Персональный ключ недоступен: на сервере нет открытого ключа провизионера") from None


async def llm_balance(conn, account_id: int) -> LlmBalance:
    bal = await wallet.balance(conn, account_id)
    has_key = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM virtual_keys WHERE account_id = $1"
                                  " AND status = 'active')", account_id)
    status = ("suspended" if bal.status not in wallet.LIVE_STATUSES else "active") if has_key else "revoked"
    return LlmBalance(coins_left=bal.coins_left, monthly_limit=bal.monthly_limit, monthly_coins=float(bal.monthly),
                      topup_coins=float(bal.carry + bal.topups), spent=float(bal.spent), period_start=bal.period_start,
                      period_end=bal.period_end, status=status)


async def _approve(conn, row: asyncpg.Record, body: Approve, coins: int, sealed: str | None, staff: Principal,
                   request: Request, payment_id: int | None) -> None:
    """Queue the create job (shared by approve and mark-paid; the caller holds the row lock).

    Without a per-client Anthropic key the account gets an LLM gateway virtual key (sealed into the job for the
    provisioner) and its first billing period starts now with `coins` monthly coins. With a per-client key the
    bot talks to Anthropic directly and the gateway is not used."""
    account_id = row["id"]
    slug = row["slug"]
    if body.slug and body.slug != slug:
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM accounts WHERE slug = $1)", body.slug):
            raise ApiError(409, "slug_taken", "Этот адрес уже занят")
        slug = body.slug
    await conn.execute(
        "UPDATE accounts SET status = 'approved', slug = $2, coins_limit = $3, reject_reason = NULL,"
        " last_error = NULL, updated_at = now() WHERE id = $1",
        account_id, slug, coins,
    )
    gateway_sealed, key_id = None, None
    if sealed is None:
        await wallet.revoke_keys(conn, account_id, "superseded by a new approval")
        key, key_id = await wallet.issue_key(conn, account_id, staff.id)
        gateway_sealed = wallet.seal_key(key)
        del key
        await wallet.start_period(conn, account_id, coins, staff_id=staff.id, note="first period")
    job_id = await jobs.enqueue(conn, account_id, "create", slug, coins=coins, llm_key_sealed=sealed, staff_id=staff.id,
                                gateway_key_sealed=gateway_sealed, key_id=key_id)
    await audit(conn, "staff.approve", staff_id=staff.id, account_id=account_id, ip=ratelimit.client_ip(request),
                details={"job": job_id, "coins": coins, "slug": slug, "slug_changed": slug != row["slug"],
                         "custom_key": sealed is not None, "payment": payment_id, "gateway_key": key_id})


@router.post("/api/staff/applications/{account_id}/approve", response_model=Ok, dependencies=staff_mutation)
async def post_approve(account_id: int, body: Approve, request: Request, staff: Principal = Depends(current_staff)):
    """Approve without a recorded payment (trial, partner, paid elsewhere); audited with payment = null."""
    sealed = _seal(body.anthropic_key)
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await _lock_account(conn, account_id)
        _require_status(row, "verified", "awaiting_payment", "rejected")
        await _approve(conn, row, body, body.coins, sealed, staff, request, None)
    return Ok()


@router.post("/api/staff/applications/{account_id}/mark-paid", response_model=Ok, dependencies=staff_mutation)
async def post_mark_paid(account_id: int, body: MarkPaid, request: Request, staff: Principal = Depends(current_staff)):
    """Record the bank payment in the ledger and approve in one transaction: payment is the gate, so a paid
    application never waits for a second click. Coins default to the tariff's monthly coins."""
    sealed = _seal(body.anthropic_key)
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await _lock_account(conn, account_id)
        _require_status(row, "awaiting_payment")
        tariff = tariffs.get(row["tariff"])
        coins = body.coins if body.coins is not None else (tariff.coins if tariff else None)
        if coins is None:
            raise ApiError(422, "validation_error", "coins: укажите лимит коинов")
        payment_id = await billing.record_payment(
            conn, account_id, invoice_no=row["invoice_no"], tariff=row["tariff"], amount_kzt=body.amount_kzt,
            reference=body.reference, note=body.note, staff_id=staff.id,
        )
        await audit(conn, "staff.mark_paid", staff_id=staff.id, account_id=account_id, ip=ratelimit.client_ip(request),
                    details={"payment": payment_id, "invoice": row["invoice_no"], "amount": body.amount_kzt,
                             "invoice_amount": row["invoice_amount"], "reference": body.reference.strip()})
        await _approve(conn, row, body, coins, sealed, staff, request, payment_id)
    return Ok()


@router.get("/api/staff/applications/{account_id}/invoice", response_model=Invoice, dependencies=staff_only)
async def get_application_invoice(account_id: int):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, email, contact_name, company, phone, slug, tariff, invoice_no, invoice_amount, invoice_issued_at"
        " FROM accounts WHERE id = $1",
        account_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "Заявка не найдена")
    return billing.invoice_from_row(row, await billing.is_paid(pool, account_id, row["invoice_no"]))


@router.post("/api/staff/applications/{account_id}/reject", response_model=Ok, dependencies=staff_mutation)
async def post_reject(account_id: int, body: Reject, request: Request, staff: Principal = Depends(current_staff)):
    reason = " ".join(body.reason.split())
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await _lock_account(conn, account_id)
        _require_status(row, "verified", "awaiting_payment", "approved", "failed")
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM jobs WHERE account_id = $1 AND status = 'claimed')",
                               account_id):
            raise ApiError(409, "job_in_progress", "Бот уже создаётся; дождитесь результата")
        # a queued create job is cancelled; sealed keys of the account are wiped
        await conn.execute(
            "UPDATE jobs SET status = 'failed', error = 'отменено: заявка отклонена', finished_at = now()"
            " WHERE account_id = $1 AND status = 'pending'",
            account_id,
        )
        await conn.execute("UPDATE jobs SET llm_key_sealed = NULL, gateway_key_sealed = NULL WHERE account_id = $1",
                           account_id)
        await wallet.revoke_keys(conn, account_id, "application rejected")
        await conn.execute(
            "UPDATE accounts SET status = 'rejected', reject_reason = $2, updated_at = now() WHERE id = $1",
            account_id, reason,
        )
        await audit(conn, "staff.reject", staff_id=staff.id, account_id=account_id, ip=ratelimit.client_ip(request),
                    details={"reason": reason})
    await mailer.send_quiet(row["email"], *letters.rejected(row["contact_name"], reason))
    return Ok()


@router.post("/api/staff/applications/{account_id}/retry", response_model=Ok, dependencies=staff_mutation)
async def post_retry(account_id: int, request: Request, staff: Principal = Depends(current_staff)):
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await _lock_account(conn, account_id)
        _require_status(row, "failed")
        last = await conn.fetchrow(
            "SELECT coins, llm_key_sealed, gateway_key_sealed, key_id FROM jobs WHERE account_id = $1 AND kind = 'create'"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            account_id,
        )
        coins = last["coins"] if last else row["coins_limit"]
        job_id = await jobs.enqueue(conn, account_id, "create", row["slug"], coins=coins,
                                    llm_key_sealed=last["llm_key_sealed"] if last else None, staff_id=staff.id,
                                    gateway_key_sealed=last["gateway_key_sealed"] if last else None,
                                    key_id=last["key_id"] if last else None)
        await conn.execute("UPDATE accounts SET status = 'approved', updated_at = now() WHERE id = $1", account_id)
        await audit(conn, "staff.retry", staff_id=staff.id, account_id=account_id, ip=ratelimit.client_ip(request),
                    details={"job": job_id})
    return Ok()


async def _simple_job(account_id: int, kind: str, allowed: tuple[str, ...], request: Request, staff: Principal,
                      confirm_slug: str | None = None) -> None:
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await _lock_account(conn, account_id)
        _require_status(row, *allowed)
        if confirm_slug is not None and confirm_slug.strip().lower() != row["slug"]:
            raise ApiError(400, "confirm_mismatch", "Введите адрес бота для подтверждения удаления")
        job_id = await jobs.enqueue(conn, account_id, kind, row["slug"], staff_id=staff.id)
        await audit(conn, f"staff.{kind}", staff_id=staff.id, account_id=account_id, ip=ratelimit.client_ip(request),
                    details={"job": job_id})


@router.post("/api/staff/applications/{account_id}/suspend", response_model=Ok, dependencies=staff_mutation)
async def post_suspend(account_id: int, request: Request, staff: Principal = Depends(current_staff)):
    await _simple_job(account_id, "suspend", ("active",), request, staff)
    return Ok()


@router.post("/api/staff/applications/{account_id}/resume", response_model=Ok, dependencies=staff_mutation)
async def post_resume(account_id: int, request: Request, staff: Principal = Depends(current_staff)):
    await _simple_job(account_id, "resume", ("suspended",), request, staff)
    return Ok()


@router.post("/api/staff/applications/{account_id}/remove", response_model=Ok, dependencies=staff_mutation)
async def post_remove(account_id: int, body: RemoveConfirm, request: Request, staff: Principal = Depends(current_staff)):
    await _simple_job(account_id, "remove", ("active", "suspended", "failed"), request, staff, body.confirm_slug)
    return Ok()


@router.get("/api/staff/audit", response_model=list[AuditOut], dependencies=staff_only)
async def get_audit(limit: int = Query(200, ge=1, le=1000)):
    return await _audit_rows(get_pool(), None, limit)



# ---- LLM gateway: keys, invoices, local installs --------------------------------------------


@router.post("/api/staff/applications/{account_id}/key/reissue", response_model=KeyIssued, dependencies=staff_mutation)
async def post_key_reissue(account_id: int, request: Request, staff: Principal = Depends(current_staff)):
    """New virtual key. Local install: the key is returned once and older keys stop at once. Cloud bot: a
    rekey job writes it into the instance .env (this also switches a bot with a direct provider key to the
    gateway); older keys are revoked only when the job succeeds."""
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT id, slug, status, local_install, period_start, coins_limit, tariff FROM accounts WHERE id = $1"
            " FOR UPDATE", account_id,
        )
        if row is None:
            raise ApiError(404, "not_found", "Заявка не найдена")
        if row["local_install"]:
            _require_status(row, "active", "suspended")
        else:
            _require_status(row, "active")
        if row["period_start"] is None:
            tariff = tariffs.get(row["tariff"])
            monthly = row["coins_limit"] if row["coins_limit"] is not None else (tariff.coins if tariff else 0)
            await wallet.start_period(conn, account_id, monthly, staff_id=staff.id, note="gateway enabled")
        key, key_id = await wallet.issue_key(conn, account_id, staff.id)
        job_id = None
        if row["local_install"]:
            await wallet.revoke_keys(conn, account_id, "replaced by a reissued key", keep=key_id)
        else:
            job_id = await jobs.enqueue(conn, account_id, "rekey", row["slug"], staff_id=staff.id,
                                        gateway_key_sealed=wallet.seal_key(key), key_id=key_id)
        await audit(conn, "staff.key_reissue", staff_id=staff.id, account_id=account_id,
                    ip=ratelimit.client_ip(request), details={"key": key_id, "job": job_id})
    return KeyIssued(prefix=wallet.display_prefix(key), key=key if row["local_install"] else None, job_id=job_id)


@router.post("/api/staff/applications/{account_id}/key/revoke", response_model=Ok, dependencies=staff_mutation)
async def post_key_revoke(account_id: int, request: Request, staff: Principal = Depends(current_staff)):
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        if await conn.fetchval("SELECT id FROM accounts WHERE id = $1 FOR UPDATE", account_id) is None:
            raise ApiError(404, "not_found", "Заявка не найдена")
        count = await wallet.revoke_keys(conn, account_id, "revoked by staff")
        await audit(conn, "staff.key_revoke", staff_id=staff.id, account_id=account_id,
                    ip=ratelimit.client_ip(request), details={"revoked": count})
    return Ok()


@router.get("/api/staff/applications/{account_id}/invoices/{number}", response_model=Invoice, dependencies=staff_only)
async def get_account_invoice(account_id: int, number: str):
    pool = get_pool()
    account = await pool.fetchrow("SELECT id, email, contact_name, company, phone, slug FROM accounts WHERE id = $1",
                                  account_id)
    inv = await pool.fetchrow(f"SELECT {billing.INVOICE_COLS} FROM invoices WHERE account_id = $1 AND number = $2",
                              account_id, number)
    if account is None or inv is None:
        raise ApiError(404, "not_found", "Счёт не найден")
    return billing.invoice_from_record(account, inv)


@router.post("/api/staff/invoices/{invoice_id}/mark-paid", response_model=Ok, dependencies=staff_mutation)
async def post_invoice_paid(invoice_id: int, body: MarkInvoicePaid, request: Request,
                            staff: Principal = Depends(current_staff)):
    """Payment of a top-up invoice (coins are added at once) or a renewal invoice (the period rolls over when
    it has ended; an account suspended for non-payment is renewed and resumed)."""
    pool = get_pool()
    notify = None
    async with pool.acquire() as conn, conn.transaction():
        inv = await conn.fetchrow(f"SELECT {billing.INVOICE_COLS} FROM invoices WHERE id = $1 FOR UPDATE", invoice_id)
        if inv is None:
            raise ApiError(404, "not_found", "Счёт не найден")
        if inv["paid_at"] is not None:
            raise ApiError(409, "already_paid", "Счёт уже отмечен как оплаченный")
        account = await conn.fetchrow("SELECT id, email, contact_name FROM accounts WHERE id = $1 FOR UPDATE",
                                      inv["account_id"])
        payment_id = await billing.record_payment(
            conn, inv["account_id"], invoice_no=inv["number"], tariff=inv["tariff"], amount_kzt=body.amount_kzt,
            reference=body.reference, note=body.note, staff_id=staff.id,
        )
        await conn.execute("UPDATE invoices SET paid_at = now(), payment_id = $2 WHERE id = $1", invoice_id, payment_id)
        details = {"invoice": inv["number"], "kind": inv["kind"], "payment": payment_id, "amount": body.amount_kzt,
                   "invoice_amount": inv["amount_kzt"], "reference": body.reference.strip()}
        if inv["kind"] == "topup":
            await conn.execute(
                "INSERT INTO coin_grants(account_id, kind, coins, invoice_no, payment_id, staff_id, note)"
                " VALUES ($1, 'topup', $2, $3, $4, $5, $6)",
                inv["account_id"], inv["coins"], inv["number"], payment_id, staff.id, body.note.strip(),
            )
            notify = (account["email"], letters.coins_added(account["contact_name"], inv["coins"]))
        else:
            details["renewal"] = await renewal.renew_if_due(conn, inv["account_id"])
        await audit(conn, "staff.invoice_paid", staff_id=staff.id, account_id=inv["account_id"],
                    ip=ratelimit.client_ip(request), details=details)
    if notify:
        await mailer.send_quiet(notify[0], *notify[1])
    return Ok()


@router.post("/api/staff/local-accounts", response_model=LocalAccountCreated, dependencies=staff_mutation)
async def post_local_account(body: LocalAccount, request: Request, staff: Principal = Depends(current_staff)):
    """Account for a bot installed on the client's own machine: active at once, no provisioning job. The
    virtual key is returned once, for the technician (ILER_LLM_GATEWAY_URL / ILER_LLM_GATEWAY_KEY)."""
    tariff = tariffs.get(body.tariff)
    if tariff is None:
        raise ApiError(422, "validation_error", "tariff: неизвестный тариф")
    coins = body.coins if body.coins is not None else tariff.coins
    # the client sets a password with "forgot password" (the e-mail below says so)
    password_hash = await security.hash_password(security.new_csrf_token())
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        try:
            account_id = await conn.fetchval(
                "INSERT INTO accounts(email, password_hash, company, contact_name, phone, slug, status, consent_at,"
                " email_verified_at, local_install, tariff, coins_limit)"
                " VALUES ($1, $2, $3, $4, $5, $6, 'active', now(), now(), true, $7, $8) RETURNING id",
                body.email, password_hash, body.company, body.contact_name, body.phone, body.slug, tariff.code, coins,
            )
        except asyncpg.UniqueViolationError:
            raise ApiError(409, "already_exists", "Аккаунт с этим e-mail или адресом уже есть") from None
        await wallet.start_period(conn, account_id, coins, staff_id=staff.id, note="local install")
        key, key_id = await wallet.issue_key(conn, account_id, staff.id)
        await audit(conn, "staff.local_account", staff_id=staff.id, account_id=account_id,
                    ip=ratelimit.client_ip(request), details={"tariff": tariff.code, "coins": coins, "key": key_id})
    await mailer.send_quiet(body.email, *letters.local_account(body.contact_name, body.email))
    return LocalAccountCreated(account_id=account_id, prefix=wallet.display_prefix(key), key=key)
