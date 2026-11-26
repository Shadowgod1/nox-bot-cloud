"""Client flows: register -> e-mail code -> tariff + invoice -> staff marks paid -> bot ready; login, reset."""

import logging

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response

from nox_portal import billing, codes, letters, mailer, ratelimit, security, slugs, tariffs, wallet
from nox_portal.config import get_settings
from nox_portal.db import get_pool
from nox_portal.deps import (
    Principal, audit, clear_session_cookies, csrf_protect, current_client, is_locked, register_failure,
    register_success, set_session_cookies,
)
from nox_portal.errors import ApiError
from nox_portal.schemas import (
    ClientMe, Credentials, EmailOnly, Invoice, InvoiceListItem, LlmBalance, Ok, Register, ResetConfirm, SetupLink,
    SlugCheck, TariffChoice, TariffOut, UsageDay, UsageModel, UsageOut, Verify,
)

log = logging.getLogger("nox_portal.client")
router = APIRouter(tags=["client"])

_ACCOUNT_COLS = ("id, email, password_hash, company, contact_name, phone, slug, status, admin_url, reject_reason,"
                 " setup_link_enc, tariff, invoice_no, invoice_amount, invoice_issued_at, created_at, period_start,"
                 " local_install")
# unverified registrations hold their slug for a day, then disappear
UNVERIFIED_TTL = "24 hours"
INVALID_CREDENTIALS = ApiError(401, "invalid_credentials", "Неверный e-mail или пароль")


def client_me(row: asyncpg.Record) -> ClientMe:
    return ClientMe(
        email=row["email"], company=row["company"], contact_name=row["contact_name"], phone=row["phone"],
        slug=row["slug"], bot_host=f"{row['slug']}.{get_settings().instance_domain}", status=row["status"],
        admin_url=row["admin_url"], reject_reason=row["reject_reason"],
        setup_link_available=bool(row["setup_link_enc"]), tariff=row["tariff"], invoice_no=row["invoice_no"],
        invoice_amount=row["invoice_amount"], created_at=row["created_at"],
        llm_enabled=row["period_start"] is not None, local_install=row["local_install"],
    )


async def _send_code(to: str, name: str, purpose: str, code: str) -> None:
    subject, text = (letters.verify_code if purpose == "verify" else letters.reset_code)(name, code)
    try:
        await mailer.send(to, subject, text)
    except mailer.MailError as exc:
        log.error("code mail to %s failed: %s", to, exc)
        raise ApiError(502, "email_failed", "Не удалось отправить письмо. Повторите попытку позже.") from None


@router.get("/api/public-config")
async def get_public_config():
    return {"instance_domain": get_settings().instance_domain}


@router.get("/api/slug-check", response_model=SlugCheck)
async def get_slug_check(request: Request, slug: str = Query(max_length=64)):
    await ratelimit.hit("slug:ip", ratelimit.client_ip(request))
    slug = slugs.normalize(slug)
    err = slugs.check(slug)
    if err:
        return SlugCheck(slug=slug, available=False, message=err)
    taken = await get_pool().fetchval(
        f"SELECT EXISTS (SELECT 1 FROM accounts WHERE slug = $1 AND NOT (status = 'new'"
        f" AND created_at < now() - interval '{UNVERIFIED_TTL}'))",
        slug,
    )
    return SlugCheck(slug=slug, available=not taken, message="этот адрес уже занят" if taken else None)


@router.post("/api/auth/register", response_model=Ok, status_code=202)
async def post_register(body: Register, request: Request):
    """Same answer whether or not the e-mail is already registered (no account enumeration)."""
    ip = ratelimit.client_ip(request)
    await ratelimit.hit("register:ip", ip)
    await ratelimit.hit("register:email", body.email)
    pool = get_pool()
    await pool.execute(f"DELETE FROM accounts WHERE status = 'new' AND created_at < now() - interval '{UNVERIFIED_TTL}'")
    password_hash = await security.hash_password(body.password)
    send: tuple[str, str] | None = None  # (purpose, code)
    async with pool.acquire() as conn, conn.transaction():
        holder = await conn.fetchval("SELECT email FROM accounts WHERE slug = $1", body.slug)
        if holder is not None and holder != body.email:
            raise ApiError(409, "slug_taken", "Этот адрес бота уже занят, выберите другой")
        existing = await conn.fetchrow("SELECT id, status, contact_name FROM accounts WHERE email = $1 FOR UPDATE",
                                       body.email)
        if existing is not None and existing["status"] != "new":
            # registered and verified already: tell the owner of the address, answer as usual
            account_id, name = existing["id"], existing["contact_name"]
            send = ("already", "")
        else:
            try:
                if existing is None:
                    account_id = await conn.fetchval(
                        "INSERT INTO accounts(email, password_hash, company, contact_name, phone, slug, consent_at,"
                        " register_ip) VALUES ($1, $2, $3, $4, $5, $6, now(), $7) RETURNING id",
                        body.email, password_hash, body.company, body.contact_name, body.phone, body.slug, ip,
                    )
                else:
                    # an unverified registration with this address is replaced (nobody proved the address yet)
                    account_id = existing["id"]
                    await conn.execute(
                        "UPDATE accounts SET password_hash = $2, company = $3, contact_name = $4, phone = $5,"
                        " slug = $6, consent_at = now(), register_ip = $7, updated_at = now() WHERE id = $1",
                        account_id, password_hash, body.company, body.contact_name, body.phone, body.slug, ip,
                    )
            except asyncpg.UniqueViolationError:
                raise ApiError(409, "slug_taken", "Этот адрес бота уже занят, выберите другой") from None
            name = body.contact_name
            if await codes.cooldown_left(conn, account_id, "verify") == 0:
                send = ("verify", await codes.issue(conn, account_id, "verify"))
    if send is not None:
        if send[0] == "already":
            try:
                await ratelimit.hit("send:email", body.email)
                await mailer.send_quiet(body.email, *letters.already_registered(name))
            except ApiError:
                pass
        else:
            await _send_code(body.email, name, "verify", send[1])
    return Ok()


@router.post("/api/auth/resend", response_model=Ok)
async def post_resend(body: EmailOnly, request: Request):
    await ratelimit.hit("send:ip", ratelimit.client_ip(request))
    await ratelimit.hit("send:email", body.email)
    pool = get_pool()
    row = await pool.fetchrow("SELECT id, contact_name FROM accounts WHERE email = $1 AND status = 'new'", body.email)
    if row is None:
        return Ok()
    left = await codes.cooldown_left(pool, row["id"], "verify")
    if left:
        raise ApiError(429, "resend_cooldown", f"Новый код можно запросить через {left} с.",
                       headers={"Retry-After": str(left)})
    code = await codes.issue(pool, row["id"], "verify")
    await _send_code(body.email, row["contact_name"], "verify", code)
    return Ok()


@router.post("/api/auth/verify", response_model=ClientMe)
async def post_verify(body: Verify, request: Request, response: Response):
    ip = ratelimit.client_ip(request)
    await ratelimit.hit("verify:ip", ip)
    pool = get_pool()
    row = await pool.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE email = $1 AND status = 'new'", body.email)
    await codes.check(pool, row["id"] if row else None, "verify", body.code)
    assert row is not None
    row = await pool.fetchrow(
        f"UPDATE accounts SET status = 'verified', email_verified_at = now(), updated_at = now()"
        f" WHERE id = $1 AND status = 'new' RETURNING {_ACCOUNT_COLS}",
        row["id"],
    )
    if row is None:
        raise ApiError(409, "already_verified", "E-mail уже подтверждён, войдите в кабинет")
    await audit(pool, "client.verified", account_id=row["id"], ip=ip)
    set_session_cookies(response, "client", row["id"], row["password_hash"])
    subject, text = letters.staff_new_application(row["company"], row["contact_name"], row["email"], row["phone"],
                                                  row["slug"])
    for to in mailer.staff_recipients():
        await mailer.send_quiet(to, subject, text)
    return client_me(row)


@router.post("/api/auth/login", response_model=ClientMe)
async def post_login(body: Credentials, request: Request, response: Response):
    """No CSRF check (no session yet); the session cookie is SameSite=Strict."""
    await ratelimit.hit("login:ip", ratelimit.client_ip(request))
    await ratelimit.hit("login:email", body.email)
    pool = get_pool()
    row = await pool.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE email = $1", body.email)
    if row is None:
        await security.verify_password(None, body.password)
        raise INVALID_CREDENTIALS
    if await is_locked(pool, "client", row["id"]):
        await security.verify_password(row["password_hash"], body.password)
        raise INVALID_CREDENTIALS
    if not await security.verify_password(row["password_hash"], body.password):
        if await register_failure(pool, "client", row["id"]):
            await audit(pool, "client.locked", account_id=row["id"], ip=ratelimit.client_ip(request))
        raise INVALID_CREDENTIALS
    await register_success(pool, "client", row["id"])
    if row["status"] == "new":
        raise ApiError(403, "email_not_verified", "Подтвердите e-mail: введите код из письма")
    set_session_cookies(response, "client", row["id"], row["password_hash"])
    return client_me(row)


@router.post("/api/auth/logout", response_model=Ok)
async def post_logout(response: Response):
    clear_session_cookies(response)
    return Ok()


@router.post("/api/auth/reset/request", response_model=Ok)
async def post_reset_request(body: EmailOnly, request: Request):
    """Always the same answer; a code is mailed only to an existing account (at most once a minute)."""
    await ratelimit.hit("send:ip", ratelimit.client_ip(request))
    await ratelimit.hit("send:email", body.email)
    pool = get_pool()
    row = await pool.fetchrow("SELECT id, contact_name FROM accounts WHERE email = $1", body.email)
    if row is None or await codes.cooldown_left(pool, row["id"], "reset"):
        return Ok()
    code = await codes.issue(pool, row["id"], "reset")
    await _send_code(body.email, row["contact_name"], "reset", code)
    return Ok()


@router.post("/api/auth/reset/confirm", response_model=Ok)
async def post_reset_confirm(body: ResetConfirm, request: Request, response: Response):
    ip = ratelimit.client_ip(request)
    await ratelimit.hit("reset:ip", ip)
    pool = get_pool()
    account_id = await pool.fetchval("SELECT id FROM accounts WHERE email = $1", body.email)
    await codes.check(pool, account_id, "reset", body.code)
    new_hash = await security.hash_password(body.password)
    # a new hash changes the session fingerprint: every older session ends
    await pool.execute(
        "UPDATE accounts SET password_hash = $2, failed_logins = 0, locked_until = NULL, updated_at = now()"
        " WHERE id = $1",
        account_id, new_hash,
    )
    await audit(pool, "client.password_reset", account_id=account_id, ip=ip)
    clear_session_cookies(response)
    return Ok()


@router.get("/api/me", response_model=ClientMe)
async def get_me(client: Principal = Depends(current_client)):
    row = await get_pool().fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = $1", client.id)
    return client_me(row)


@router.post("/api/me/setup-link", response_model=SetupLink, dependencies=[Depends(current_client), Depends(csrf_protect)])
async def post_reveal_setup_link(request: Request, client: Principal = Depends(current_client)):
    """The owner setup link is shown exactly once, then erased from the portal."""
    pool = get_pool()
    enc = await pool.fetchval(
        "UPDATE accounts a SET setup_link_enc = NULL, updated_at = now()"
        " FROM (SELECT id, setup_link_enc FROM accounts WHERE id = $1 FOR UPDATE) old"
        " WHERE a.id = old.id RETURNING old.setup_link_enc",
        client.id,
    )
    if not enc:
        raise ApiError(404, "setup_link_gone", "Ссылка уже была показана. Её копия есть в письме «Ваш бот готов».")
    await audit(pool, "client.setup_link_revealed", account_id=client.id, ip=ratelimit.client_ip(request))
    return SetupLink(setup_url=security.decrypt(enc))


# ---- tariff and invoice ---------------------------------------------------------------------


@router.get("/api/tariffs", response_model=list[TariffOut])
async def get_tariffs():
    return tariffs.as_dicts()


@router.post("/api/me/tariff", response_model=ClientMe, dependencies=[Depends(current_client), Depends(csrf_protect)])
async def post_choose_tariff(body: TariffChoice, request: Request, client: Principal = Depends(current_client)):
    """Pick a tariff after verification: the invoice is issued (or re-issued for another tariff while unpaid)
    and e-mailed; the application waits for payment."""
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await billing.choose_tariff(conn, client.id, body.tariff)
        await audit(conn, "client.tariff_chosen", account_id=client.id, ip=ratelimit.client_ip(request),
                    details={"tariff": row["tariff"], "invoice": row["invoice_no"], "amount": row["invoice_amount"]})
    tariff = tariffs.get(row["tariff"])
    assert tariff is not None
    amount = tariffs.money(row["invoice_amount"])
    await mailer.send_quiet(row["email"], *letters.invoice(row["contact_name"], row["invoice_no"], tariff.name, amount,
                                                           get_settings().invoice_due_days))
    subject, text = letters.staff_tariff_chosen(row["company"], row["invoice_no"], tariff.name, amount)
    for to in mailer.staff_recipients():
        await mailer.send_quiet(to, subject, text)
    me = await pool.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = $1", client.id)
    return client_me(me)


@router.get("/api/me/invoice", response_model=Invoice)
async def get_invoice(client: Principal = Depends(current_client)):
    pool = get_pool()
    row = await pool.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = $1", client.id)
    return billing.invoice_from_row(row, await billing.is_paid(pool, client.id, row["invoice_no"]))


# ---- LLM gateway: balance, usage, top-up, invoices --------------------------------------------


async def _llm_balance(conn, account_id: int) -> LlmBalance:
    from nox_portal.api.staff import llm_balance

    return await llm_balance(conn, account_id)


@router.get("/api/me/usage", response_model=UsageOut)
async def get_usage(client: Principal = Depends(current_client)):
    """Balance and spend of the current period, by day (portal time zone) and by model."""
    pool = get_pool()
    acc = await pool.fetchrow("SELECT period_start, counted_since FROM accounts WHERE id = $1", client.id)
    if acc["period_start"] is None:
        raise ApiError(404, "llm_disabled", "Бот не подключён к балансу коинов Nox")
    by_day = await pool.fetch(
        "SELECT to_char((created_at AT TIME ZONE $3)::date, 'YYYY-MM-DD') AS day, sum(coins) AS coins"
        " FROM llm_usage WHERE account_id = $1 AND created_at >= $2 GROUP BY 1 ORDER BY 1",
        client.id, acc["counted_since"], get_settings().tz,
    )
    by_model = await pool.fetch(
        "SELECT provider, model, count(*) AS requests, sum(input_tokens + cache_write_tokens + cache_read_tokens)"
        " AS input_tokens, sum(output_tokens) AS output_tokens, sum(coins) AS coins FROM llm_usage"
        " WHERE account_id = $1 AND created_at >= $2 GROUP BY 1, 2 ORDER BY coins DESC",
        client.id, acc["counted_since"],
    )
    return UsageOut(
        balance=await _llm_balance(pool, client.id),
        by_day=[UsageDay(day=r["day"], coins=float(r["coins"])) for r in by_day],
        by_model=[UsageModel(provider=r["provider"], model=r["model"], requests=r["requests"],
                             input_tokens=r["input_tokens"], output_tokens=r["output_tokens"], coins=float(r["coins"]))
                  for r in by_model],
    )


@router.post("/api/me/topup", response_model=Invoice, dependencies=[Depends(current_client), Depends(csrf_protect)])
async def post_topup(request: Request, client: Principal = Depends(current_client)):
    """Invoice for a coin pack (an unpaid one from the last 30 days is shown again instead of a new one)."""
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        acc = await conn.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = $1 FOR UPDATE", client.id)
        if acc["period_start"] is None or acc["status"] in ("removed", "rejected"):
            raise ApiError(409, "llm_disabled", "Пополнение доступно после подключения бота")
        inv, created = await billing.topup_invoice(conn, client.id)
        if created:
            await audit(conn, "client.topup_invoice", account_id=client.id, ip=ratelimit.client_ip(request),
                        details={"invoice": inv["number"], "amount": inv["amount_kzt"], "coins": inv["coins"]})
    if created:
        amount = tariffs.money(inv["amount_kzt"])
        await mailer.send_quiet(acc["email"], *letters.topup_invoice(acc["contact_name"], inv["number"], amount,
                                                                     inv["coins"]))
        for to in mailer.staff_recipients():
            await mailer.send_quiet(to, *letters.staff_invoice_issued(acc["company"], inv["number"], "Пакет коинов",
                                                                      amount))
    return billing.invoice_from_record(acc, inv)


@router.get("/api/me/invoices", response_model=list[InvoiceListItem])
async def get_invoices(client: Principal = Depends(current_client)):
    return await billing.invoices_of(get_pool(), client.id)


@router.get("/api/me/invoices/{number}", response_model=Invoice)
async def get_invoice_by_number(number: str, client: Principal = Depends(current_client)):
    pool = get_pool()
    acc = await pool.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = $1", client.id)
    if number == acc["invoice_no"]:
        return billing.invoice_from_row(acc, await billing.is_paid(pool, client.id, acc["invoice_no"]))
    inv = await pool.fetchrow(f"SELECT {billing.INVOICE_COLS} FROM invoices WHERE account_id = $1 AND number = $2",
                              client.id, number)
    if inv is None:
        raise ApiError(404, "not_found", "Счёт не найден")
    return billing.invoice_from_record(acc, inv)
