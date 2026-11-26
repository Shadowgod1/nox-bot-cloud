"""Client flows: register -> e-mail code -> tariff (Free at once | invoice via a manager | online) -> bot ready;
login, reset, top-ups, paying invoices online."""

import logging

import asyncpg
from fastapi import APIRouter, Depends, Query, Request, Response

from nox_portal import billing, codes, fulfil, letters, mailer, payments, ratelimit, security, slugs, tariffs, wallet
from nox_portal.config import get_settings
from nox_portal.db import get_pool
from nox_portal.deps import (
    Principal, audit, clear_session_cookies, csrf_protect, current_client, is_locked, register_failure,
    register_success, set_session_cookies,
)
from nox_portal.errors import ApiError
from nox_portal.payments import service as online
from nox_portal.schemas import (
    CheckoutOut, ClientMe, Credentials, EmailOnly, Invoice, InvoiceListItem, LlmBalance, Ok, Register, ResetConfirm,
    SetupLink, SlugCheck, TariffChoice, TariffOut, TopupChoice, TopupOut, UsageDay, UsageModel, UsageOut, Verify,
)

log = logging.getLogger("nox_portal.client")
router = APIRouter(tags=["client"])

_ACCOUNT_COLS = ("id, email, password_hash, company, contact_name, phone, slug, status, admin_url, reject_reason,"
                 " setup_link_enc, tariff, invoice_no, invoice_amount, invoice_issued_at, created_at, period_start,"
                 " local_install, invoice_note, pay_method, free_until, suspend_reason, upgrade_tariff")
# unverified registrations hold their slug for a day, then disappear
UNVERIFIED_TTL = "24 hours"
INVALID_CREDENTIALS = ApiError(401, "invalid_credentials", "Неверный e-mail или пароль")


def client_me(row: asyncpg.Record, payment_url: str | None = None) -> ClientMe:
    return ClientMe(
        email=row["email"], company=row["company"], contact_name=row["contact_name"], phone=row["phone"],
        slug=row["slug"], bot_host=f"{row['slug']}.{get_settings().instance_domain}", status=row["status"],
        admin_url=row["admin_url"], reject_reason=row["reject_reason"],
        setup_link_available=bool(row["setup_link_enc"]), tariff=row["tariff"], invoice_no=row["invoice_no"],
        invoice_amount=row["invoice_amount"], created_at=row["created_at"],
        llm_enabled=row["period_start"] is not None, local_install=row["local_install"],
        pay_method=row["pay_method"], payment_url=payment_url, free_until=row["free_until"],
        suspend_reason=row["suspend_reason"], upgrade_tariff=row["upgrade_tariff"],
    )


async def _payment_url(conn, account_id: int) -> str | None:
    """Checkout page of the latest pending online payment for the subscription / upgrade (cabinet button)."""
    return await conn.fetchval(
        "SELECT redirect_url FROM online_payments WHERE account_id = $1 AND status = 'pending'"
        " AND purpose IN ('subscription','upgrade') AND redirect_url IS NOT NULL"
        " ORDER BY created_at DESC, id DESC LIMIT 1", account_id)


async def _me(conn, account_id: int) -> ClientMe:
    row = await conn.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = $1", account_id)
    return client_me(row, await _payment_url(conn, account_id))


async def _send_code(to: str, name: str, purpose: str, code: str) -> None:
    subject, text = (letters.verify_code if purpose == "verify" else letters.reset_code)(name, code)
    try:
        await mailer.send(to, subject, text)
    except mailer.MailError as exc:
        log.error("code mail to %s failed: %s", to, exc)
        raise ApiError(502, "email_failed", "Не удалось отправить письмо. Повторите попытку позже.") from None


@router.get("/api/public-config")
async def get_public_config():
    provider = payments.get_provider()
    return {"instance_domain": get_settings().instance_domain, "online_payments": provider is not None,
            "online_methods": sorted(provider.supports) if provider else []}


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
    return await _me(get_pool(), client.id)


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


FREE_FULL = "Тестовые места закончились, выберите тариф или напишите менеджеру"
FREE_USED = "Тестовый период уже использован для этого e-mail или телефона. Выберите платный тариф."
# an issued but unpaid subscription invoice is withdrawn when the client changes the choice (the number stays
# reserved and is reused when a manager issues the next one)
VOID_INVOICE = " invoice_amount = NULL, invoice_issued_at = NULL, invoice_note = '',"


def phone_digits(phone: str) -> str:
    """+7 701 000-00-00 / 8 701 000 00 00 -> 77010000000 (one Free trial per phone)."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return digits


async def free_slots_left(conn) -> int:
    used = await conn.fetchval("SELECT count(*) FROM accounts WHERE tariff = 'free'"
                               " AND status IN ('approved','provisioning','active')")
    return max(get_settings().free_max_active - used, 0)


@router.get("/api/tariffs", response_model=list[TariffOut])
async def get_tariffs():
    """Paid tariffs, then Free (test) when NOX_FREE_TARIFF_PUBLIC; available=false when every test place is taken."""
    out = [TariffOut(**t) for t in tariffs.as_dicts()]
    if get_settings().free_tariff_public:
        f = tariffs.FREE
        out.append(TariffOut(code=f.code, name=f.name, price_kzt=f.price_kzt, coins=f.coins, description=f.description,
                             available=await free_slots_left(get_pool()) > 0))
    return out


async def _start_free(conn, row, ip: str) -> list:
    """Free (test): approved at once — global cap, one trial per e-mail / phone ever."""
    if not get_settings().free_tariff_public:
        raise ApiError(422, "validation_error", "tariff: неизвестный тариф")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('nox_portal_free'))")
    if await free_slots_left(conn) <= 0:
        raise ApiError(409, "free_full", FREE_FULL)
    phone = phone_digits(row["phone"])
    if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM free_trials WHERE email = $1 OR phone = $2)",
                           row["email"], phone):
        raise ApiError(409, "free_used", FREE_USED)
    await conn.execute("INSERT INTO free_trials(account_id, email, phone) VALUES ($1, $2, $3)",
                       row["id"], row["email"], phone)
    await conn.execute(f"UPDATE accounts SET{VOID_INVOICE} pay_method = 'free' WHERE id = $1", row["id"])
    await fulfil.approve(conn, row, coins=tariffs.FREE.coins, tariff=tariffs.FREE.code, staff_id=None, ip=ip,
                         payment_id=None, action="client.auto_free")
    return [(to, letters.staff_free_started(row["company"], row["email"], row["slug"]))
            for to in mailer.staff_recipients()]


@router.post("/api/me/tariff", response_model=ClientMe, dependencies=[Depends(current_client), Depends(csrf_protect)])
async def post_choose_tariff(body: TariffChoice, request: Request, client: Principal = Depends(current_client)):
    """After verification: Free -> approved at once; a paid tariff -> awaiting_invoice (a manager issues the invoice)
    or payment_pending (online checkout). A Free account with a bot chooses a paid tariff here to upgrade: by
    invoice (request to the manager) or online (upgrade invoice paid at once). Choices can change until paid."""
    tariff = tariffs.get(body.tariff)
    if tariff is None:
        raise ApiError(422, "validation_error", "tariff: неизвестный тариф")
    free = tariffs.is_free(tariff.code)
    if body.method == "online" and not free and payments.get_provider() is None:
        raise online.disabled()
    ip = ratelimit.client_ip(request)
    pool = get_pool()
    mails: list = []
    checkout: dict | None = None
    async with pool.acquire() as conn, conn.transaction():
        row = await fulfil.lock_account(conn, client.id)
        amount = tariffs.money(tariff.price_kzt)
        if row["status"] in fulfil.PAYABLE:
            if free:
                mails = await _start_free(conn, row, ip)
            elif body.method == "online":
                await conn.execute(
                    f"UPDATE accounts SET status = 'payment_pending', tariff = $2, pay_method = 'online',"
                    f"{VOID_INVOICE} updated_at = now() WHERE id = $1", client.id, tariff.code)
                checkout = {"purpose": "subscription", "amount_kzt": tariff.price_kzt, "tariff": tariff.code,
                            "coins": tariff.coins, "description": f"Nox Bot, тариф «{tariff.name}», 1 месяц"}
            else:
                await conn.execute(
                    f"UPDATE accounts SET status = 'awaiting_invoice', tariff = $2, pay_method = 'invoice',"
                    f"{VOID_INVOICE} updated_at = now() WHERE id = $1", client.id, tariff.code)
                mails = [(to, letters.staff_invoice_requested(row["company"], tariff.name, amount, client.id))
                         for to in mailer.staff_recipients()]
        elif tariffs.is_free(row["tariff"]) and row["status"] in fulfil.UPGRADABLE and not free:
            if body.method == "online":
                inv = await billing.open_invoice(conn, client.id, "upgrade")
                if inv is None or inv["tariff"] != tariff.code:
                    inv = await billing.insert_invoice(conn, client.id, "upgrade", tariff.code, tariff.price_kzt,
                                                       tariff.coins, note="онлайн-оплата")
                await conn.execute("UPDATE accounts SET upgrade_tariff = NULL WHERE id = $1", client.id)
                checkout = {"purpose": "upgrade", "amount_kzt": inv["amount_kzt"], "tariff": tariff.code,
                            "coins": inv["coins"], "invoice_no": inv["number"],
                            "description": f"Nox Bot, переход на тариф «{tariff.name}», 1 месяц"}
            else:
                await conn.execute("UPDATE accounts SET upgrade_tariff = $2, updated_at = now() WHERE id = $1",
                                   client.id, tariff.code)
                mails = [(to, letters.staff_upgrade_requested(row["company"], tariff.name, amount, client.id))
                         for to in mailer.staff_recipients()]
        else:
            raise ApiError(409, "wrong_status", "Тариф сейчас сменить нельзя: напишите менеджеру")
        await audit(conn, "client.tariff_chosen", account_id=client.id, ip=ip,
                    details={"tariff": tariff.code, "method": "free" if free else body.method,
                             "status": row["status"]})
    for to, mail in mails:
        await mailer.send_quiet(to, *mail)
    if checkout is not None:
        await online.start(pool, client.id, **checkout)
    return await _me(pool, client.id)


@router.get("/api/me/invoice", response_model=Invoice)
async def get_invoice(client: Principal = Depends(current_client)):
    pool = get_pool()
    row = await pool.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = $1", client.id)
    return billing.invoice_from_row(row, await billing.is_paid(pool, client.id, row["invoice_no"]))


@router.post("/api/me/invoices/{number}/pay-online", response_model=CheckoutOut,
             dependencies=[Depends(current_client), Depends(csrf_protect)])
async def post_pay_invoice_online(number: str, client: Principal = Depends(current_client)):
    """Pay an issued unpaid invoice (subscription, renewal, top-up, upgrade) online instead of a bank transfer."""
    if payments.get_provider() is None:
        raise online.disabled()
    pool = get_pool()
    acc = await pool.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = $1", client.id)
    if number == acc["invoice_no"] and acc["invoice_amount"] is not None:
        if acc["status"] != "awaiting_payment" or await billing.is_paid(pool, client.id, number):
            raise ApiError(409, "already_paid", "Этот счёт уже оплачен или больше не действует")
        tariff = tariffs.get(acc["tariff"])
        created = await online.start(pool, client.id, purpose="subscription", amount_kzt=acc["invoice_amount"],
                                     tariff=acc["tariff"], coins=tariff.coins if tariff else None, invoice_no=number,
                                     description=f"Nox Bot, оплата по счёту {number}")
        return CheckoutOut(redirect_url=created.redirect_url, qr_payload=created.qr_payload)
    inv = await pool.fetchrow(f"SELECT {billing.INVOICE_COLS} FROM invoices WHERE account_id = $1 AND number = $2",
                              client.id, number)
    if inv is None:
        raise ApiError(404, "not_found", "Счёт не найден")
    if inv["paid_at"] is not None:
        raise ApiError(409, "already_paid", "Этот счёт уже оплачен")
    created = await online.start(pool, client.id, purpose=inv["kind"], amount_kzt=inv["amount_kzt"],
                                 tariff=inv["tariff"], coins=inv["coins"], invoice_no=number,
                                 description=f"Nox Bot, оплата по счёту {number}")
    return CheckoutOut(redirect_url=created.redirect_url, qr_payload=created.qr_payload)


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


@router.post("/api/me/topup", response_model=TopupOut, dependencies=[Depends(current_client), Depends(csrf_protect)])
async def post_topup(request: Request, body: TopupChoice | None = None, client: Principal = Depends(current_client)):
    """Coin pack: online checkout, or a request to the manager (who issues the invoice). An unpaid top-up invoice
    is shown again instead of a new request."""
    body = body or TopupChoice()
    pool = get_pool()
    acc = await pool.fetchrow(f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = $1", client.id)
    if acc["period_start"] is None or acc["status"] in ("removed", "rejected"):
        raise ApiError(409, "llm_disabled", "Пополнение доступно после подключения бота")
    pack = tariffs.TOPUP
    if body.method == "online":
        created = await online.start(pool, client.id, purpose="topup", amount_kzt=pack.price_kzt, coins=pack.coins,
                                     tariff=pack.code, description=f"Nox Bot, пакет {pack.coins} коинов")
        return TopupOut(kind="online", redirect_url=created.redirect_url)
    created_request = False
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT 1 FROM accounts WHERE id = $1 FOR UPDATE", client.id)
        inv = await billing.open_topup_invoice(conn, client.id)
        if inv is not None:
            return TopupOut(kind="invoice", invoice_number=inv["number"])
        if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM topup_requests WHERE account_id = $1"
                                   " AND closed_at IS NULL)", client.id):
            await conn.execute("INSERT INTO topup_requests(account_id) VALUES ($1)", client.id)
            await audit(conn, "client.topup_requested", account_id=client.id, ip=ratelimit.client_ip(request))
            created_request = True
    if created_request:
        for to in mailer.staff_recipients():
            await mailer.send_quiet(to, *letters.staff_topup_requested(acc["company"], client.id))
    return TopupOut(kind="request")


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
