"""Nox staff: review applications, approve / reject / retry / suspend / resume / remove, audit log.
Every endpoint here requires a staff session; mutations also require the CSRF header."""

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response

from nox_portal import jobs, letters, mailer, ratelimit, security
from nox_portal.config import get_settings
from nox_portal.db import get_pool
from nox_portal.deps import (
    Principal, audit, clear_session_cookies, csrf_protect, current_staff, is_locked, register_failure,
    register_success, set_session_cookies,
)
from nox_portal.errors import ApiError
from nox_portal.schemas import (
    ApplicationDetail, ApplicationListItem, Approve, AuditOut, Credentials, JobOut, Ok, Reject, RemoveConfirm,
    StaffConfig, StaffMe, Status,
)

router = APIRouter(tags=["staff"])
staff_only = [Depends(current_staff)]
# auth first (401 for anonymous), then CSRF (403); FastAPI caches current_staff per request
staff_mutation = [Depends(current_staff), Depends(csrf_protect)]

COIN_PRESETS = [30000, 100000, 300000]
_LIST_COLS = "id, company, contact_name, email, phone, slug, status, coins_limit, created_at, updated_at"


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
    return StaffConfig(coin_presets=COIN_PRESETS, instance_domain=get_settings().instance_domain,
                       custom_key_supported=sealing)


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
                  has_custom_key=r["has_custom_key"], error=r["error"], created_at=r["created_at"],
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
        f"SELECT {_LIST_COLS}, admin_url, port, reject_reason, last_error, email_verified_at, register_ip"
        " FROM accounts WHERE id = $1",
        account_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "Заявка не найдена")
    job_rows = await pool.fetch(
        "SELECT id, kind, status, slug, coins, llm_key_sealed IS NOT NULL AS has_custom_key, error, created_at,"
        " claimed_at, finished_at FROM jobs WHERE account_id = $1 ORDER BY created_at DESC, id DESC LIMIT 50",
        account_id,
    )
    return ApplicationDetail(
        **dict(row), bot_host=f"{row['slug']}.{get_settings().instance_domain}",
        jobs=[_job_out(r) for r in job_rows], audit=await _audit_rows(pool, account_id, 100),
    )


async def _lock_account(conn, account_id: int) -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT id, email, contact_name, slug, status, coins_limit FROM accounts WHERE id = $1 FOR UPDATE",
        account_id,
    )
    if row is None:
        raise ApiError(404, "not_found", "Заявка не найдена")
    return row


def _require_status(row: asyncpg.Record, *allowed: str) -> None:
    if row["status"] not in allowed:
        raise ApiError(409, "wrong_status", f"Действие недоступно в статусе «{row['status']}»")


@router.post("/api/staff/applications/{account_id}/approve", response_model=Ok, dependencies=staff_mutation)
async def post_approve(account_id: int, body: Approve, request: Request, staff: Principal = Depends(current_staff)):
    sealed = None
    if body.anthropic_key:
        try:
            sealed = security.seal_for_provisioner(body.anthropic_key)
        except security.SealingUnavailable:
            raise ApiError(503, "custom_key_unavailable",
                           "Персональный ключ недоступен: на сервере нет открытого ключа провизионера") from None
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await _lock_account(conn, account_id)
        _require_status(row, "verified", "rejected")
        slug = row["slug"]
        if body.slug and body.slug != slug:
            if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM accounts WHERE slug = $1)", body.slug):
                raise ApiError(409, "slug_taken", "Этот адрес уже занят")
            slug = body.slug
        await conn.execute(
            "UPDATE accounts SET status = 'approved', slug = $2, coins_limit = $3, reject_reason = NULL,"
            " last_error = NULL, updated_at = now() WHERE id = $1",
            account_id, slug, body.coins,
        )
        job_id = await jobs.enqueue(conn, account_id, "create", slug, coins=body.coins, llm_key_sealed=sealed,
                                    staff_id=staff.id)
        await audit(conn, "staff.approve", staff_id=staff.id, account_id=account_id, ip=ratelimit.client_ip(request),
                    details={"job": job_id, "coins": body.coins, "slug": slug, "slug_changed": slug != row["slug"],
                             "custom_key": sealed is not None})
    return Ok()


@router.post("/api/staff/applications/{account_id}/reject", response_model=Ok, dependencies=staff_mutation)
async def post_reject(account_id: int, body: Reject, request: Request, staff: Principal = Depends(current_staff)):
    reason = " ".join(body.reason.split())
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await _lock_account(conn, account_id)
        _require_status(row, "verified", "approved", "failed")
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM jobs WHERE account_id = $1 AND status = 'claimed')",
                               account_id):
            raise ApiError(409, "job_in_progress", "Бот уже создаётся; дождитесь результата")
        # a queued create job is cancelled; sealed keys of the account are wiped
        await conn.execute(
            "UPDATE jobs SET status = 'failed', error = 'отменено: заявка отклонена', finished_at = now()"
            " WHERE account_id = $1 AND status = 'pending'",
            account_id,
        )
        await conn.execute("UPDATE jobs SET llm_key_sealed = NULL WHERE account_id = $1", account_id)
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
            "SELECT coins, llm_key_sealed FROM jobs WHERE account_id = $1 AND kind = 'create'"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            account_id,
        )
        coins = last["coins"] if last else row["coins_limit"]
        job_id = await jobs.enqueue(conn, account_id, "create", row["slug"], coins=coins,
                                    llm_key_sealed=last["llm_key_sealed"] if last else None, staff_id=staff.id)
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

