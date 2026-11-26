"""Billing periods of gateway accounts: renewal reminders, rollover, grace and suspension, Free trials; daily summary.

Runs as a background task of the portal api (every NOX_BILLING_TICK_SECONDS, one runner at a time via a
Postgres advisory lock). It is pure database + e-mail work, so it lives next to the data instead of in the
root provisioner on the host, which only does what needs root (docker, files, Cloudflare); suspending a
cloud bot still goes through the provisioner as an ordinary suspend job.

  period_end - NOX_RENEWAL_INVOICE_DAYS  staff notice "time to issue the renewal invoice" + client reminder;
                                         the renewal invoice itself is a staff action (one click in the card)
  period_end, renewal paid               rollover: next period, fresh monthly grant, top-ups carry over
  period_end, unpaid                     grace: the bot keeps working on what is left of the old period
  period_end + NOX_GRACE_DAYS, unpaid    status suspended (suspend_reason unpaid): keys answer 401, a cloud
                                         bot gets a suspend job; paying the invoice renews and resumes it
  Free (tariff free)                     no renewal at all (one trial grant); free_until passed -> suspended
                                         (suspend_reason trial_ended) until an upgrade invoice is paid
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import asyncpg

from nox_portal import alerts, jobs, letters, mailer, tariffs, wallet
from nox_portal.config import get_settings
from nox_portal.db import get_pool
from nox_portal.deps import audit
from nox_portal.errors import ApiError

log = logging.getLogger("nox_portal.renewal")
LOW_BALANCE_LEVELS = (20, 5)
_LOCK = "hashtext('nox_portal_billing')"


async def _renewal_paid(conn: asyncpg.Connection, account_id: int, period_start: datetime) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, number FROM invoices WHERE account_id = $1 AND kind = 'renewal' AND period_start = $2"
        " AND paid_at IS NOT NULL",
        account_id, period_start,
    )


async def renew_if_due(conn: asyncpg.Connection, account_id: int) -> dict | None:
    """Roll the period over when it has ended and its renewal is paid; resume an account suspended for
    non-payment. Caller holds a transaction. Returns what happened (for the audit log) or None."""
    acc = await conn.fetchrow(
        "SELECT id, slug, status, local_install, coins_limit, period_end, suspend_reason FROM accounts"
        " WHERE id = $1 FOR UPDATE",
        account_id,
    )
    if acc is None or acc["period_end"] is None or acc["status"] in ("removed", "rejected"):
        return None
    if await conn.fetchval("SELECT clock_timestamp() < $1", acc["period_end"]):
        return None
    paid = await _renewal_paid(conn, account_id, acc["period_end"])
    if paid is None:
        return None
    result = await wallet.rollover(conn, account_id, grace_days=get_settings().grace_days,
                                   monthly_coins=acc["coins_limit"] or 0, note=f"renewal {paid['number']}")
    if acc["status"] == "suspended" and acc["suspend_reason"] == "unpaid":
        if acc["local_install"]:
            await conn.execute("UPDATE accounts SET status = 'active', suspend_reason = NULL, updated_at = now()"
                               " WHERE id = $1", account_id)
        else:
            await conn.execute("UPDATE accounts SET suspend_reason = NULL WHERE id = $1", account_id)
            try:
                result["resume_job"] = await jobs.enqueue(conn, account_id, "resume", acc["slug"])
            except ApiError:
                log.warning("account %s: renewal paid but another job is open; resume it by hand", account_id)
    await audit(conn, "billing.renewed", account_id=account_id, details={**result, "invoice": paid["number"]})
    return result


async def renewal_notices() -> int:
    """NOX_RENEWAL_INVOICE_DAYS before the period ends: staff are asked to issue the renewal invoice (one click in
    the card) and the client gets a reminder. Once per period; Free accounts never renew."""
    s = get_settings()
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT a.id FROM accounts a WHERE a.period_end IS NOT NULL AND a.status IN ('approved','provisioning','active')"
        " AND coalesce(a.tariff, '') <> 'free' AND a.period_end - make_interval(days => $1) <= now()"
        " AND a.renewal_notice_for IS DISTINCT FROM a.period_end"
        " AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.account_id = a.id AND i.kind = 'renewal'"
        "                 AND i.period_start = a.period_end)",
        s.renewal_invoice_days,
    )
    sent = 0
    for r in rows:
        async with pool.acquire() as conn, conn.transaction():
            acc = await conn.fetchrow(
                "UPDATE accounts SET renewal_notice_for = period_end WHERE id = $1"
                " AND renewal_notice_for IS DISTINCT FROM period_end"
                " RETURNING id, email, contact_name, company, tariff, period_end", r["id"],
            )
            if acc is None:
                continue
            await audit(conn, "billing.renewal_due", account_id=acc["id"],
                        details={"period_end": acc["period_end"].isoformat()})
        sent += 1
        tariff = tariffs.get(acc["tariff"]) or tariffs.TARIFFS[0]
        await mailer.send_quiet(acc["email"], *letters.renewal_reminder(acc["contact_name"], acc["period_end"],
                                                                        s.grace_days))
        for to in mailer.staff_recipients():
            await mailer.send_quiet(to, *letters.staff_renewal_due(acc["company"], acc["period_end"], tariff.name,
                                                                   tariffs.money(tariff.price_kzt), acc["id"]))
    return sent


async def suspend_trials() -> int:
    """Free accounts whose trial (NOX_FREE_DAYS) is over: suspended (suspend_reason trial_ended), a cloud bot gets
    a suspend job; paying a paid tariff (upgrade invoice) lifts it with the same bot."""
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id FROM accounts WHERE tariff = 'free' AND status = 'active' AND free_until IS NOT NULL"
        " AND free_until <= now()"
    )
    count = 0
    for r in rows:
        async with pool.acquire() as conn, conn.transaction():
            acc = await conn.fetchrow(
                "SELECT id, slug, status, tariff, local_install, email, contact_name, company, free_until FROM accounts"
                " WHERE id = $1 FOR UPDATE", r["id"],
            )
            if acc["status"] != "active" or acc["tariff"] != "free" or acc["free_until"] is None:
                continue
            await conn.execute("UPDATE accounts SET status = 'suspended', suspend_reason = 'trial_ended',"
                               " updated_at = now() WHERE id = $1", acc["id"])
            job_id = None
            if not acc["local_install"]:
                try:
                    job_id = await jobs.enqueue(conn, acc["id"], "suspend", acc["slug"])
                except ApiError:
                    log.warning("account %s: trial ended, but another job is open", acc["id"])
            await audit(conn, "billing.trial_ended", account_id=acc["id"], details={"job": job_id})
        count += 1
        await mailer.send_quiet(acc["email"], *letters.trial_ended(acc["contact_name"]))
        for to in mailer.staff_recipients():
            await mailer.send_quiet(to, *letters.staff_trial_ended(acc["company"]))
    return count


async def rollover_due() -> int:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT a.id FROM accounts a WHERE a.period_end IS NOT NULL AND a.period_end <= now()"
        " AND a.status NOT IN ('removed','rejected') AND EXISTS (SELECT 1 FROM invoices i WHERE i.account_id = a.id"
        " AND i.kind = 'renewal' AND i.period_start = a.period_end AND i.paid_at IS NOT NULL)"
    )
    done = 0
    for r in rows:
        async with pool.acquire() as conn, conn.transaction():
            if await renew_if_due(conn, r["id"]):
                done += 1
    return done


async def suspend_overdue() -> int:
    s = get_settings()
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT a.id FROM accounts a WHERE a.period_end IS NOT NULL AND a.status = 'active'"
        " AND coalesce(a.tariff, '') <> 'free' AND a.period_end + make_interval(days => $1) <= now()"
        " AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.account_id = a.id AND i.kind = 'renewal'"
        "                 AND i.period_start = a.period_end AND i.paid_at IS NOT NULL)",
        s.grace_days,
    )
    count = 0
    for r in rows:
        async with pool.acquire() as conn, conn.transaction():
            acc = await conn.fetchrow(
                "SELECT id, slug, status, local_install, email, contact_name, company, period_end FROM accounts"
                " WHERE id = $1 FOR UPDATE", r["id"],
            )
            if acc["status"] != "active":
                continue
            number = await conn.fetchval(
                "SELECT number FROM invoices WHERE account_id = $1 AND kind = 'renewal' AND period_start = $2",
                acc["id"], acc["period_end"],
            ) or "—"
            await conn.execute("UPDATE accounts SET status = 'suspended', suspend_reason = 'unpaid', updated_at = now()"
                               " WHERE id = $1", acc["id"])
            job_id = None
            if not acc["local_install"]:
                try:
                    job_id = await jobs.enqueue(conn, acc["id"], "suspend", acc["slug"])
                except ApiError:
                    log.warning("account %s suspended for non-payment, but another job is open", acc["id"])
            await audit(conn, "billing.suspended_unpaid", account_id=acc["id"], details={"invoice": number, "job": job_id})
        count += 1
        await mailer.send_quiet(acc["email"], *letters.suspended_unpaid(acc["contact_name"]))
        for to in mailer.staff_recipients():
            await mailer.send_quiet(to, *letters.staff_suspended_unpaid(acc["company"], number))
    return count


async def low_balance_notices() -> int:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, email, contact_name, low_balance_sent FROM accounts WHERE period_end IS NOT NULL"
        " AND status IN ('approved','provisioning','active') AND low_balance_sent > $1",
        min(LOW_BALANCE_LEVELS),
    )
    sent = 0
    for r in rows:
        bal = await wallet.balance(pool, r["id"])
        if bal.total <= 0:
            continue
        percent = bal.left / bal.total * 100
        due = [lvl for lvl in LOW_BALANCE_LEVELS if percent <= lvl and r["low_balance_sent"] > lvl]
        if not due:
            continue
        level = min(due)
        updated = await pool.fetchval(
            "UPDATE accounts SET low_balance_sent = $2 WHERE id = $1 AND low_balance_sent > $2 RETURNING id",
            r["id"], level,
        )
        if updated:
            sent += 1
            await mailer.send_quiet(r["email"], *letters.low_balance(r["contact_name"], bal.coins_left,
                                                                     max(int(percent), 0)))
    return sent


# ---- daily summary --------------------------------------------------------------------------


async def _state_get(conn, key: str):
    return await conn.fetchval("SELECT value FROM portal_state WHERE key = $1", key)


async def _state_set(conn, key: str, value) -> None:
    await conn.execute(
        "INSERT INTO portal_state(key, value) VALUES ($1, $2)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        key, value,
    )


async def daily_summary(day: date) -> str:
    """Coins charged per provider on one local day (for reconciliation with the provider consoles);
    sent to staff e-mails and Telegram. Returns the text."""
    tz = ZoneInfo(get_settings().tz)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT provider, count(*) AS n, sum(coins) AS coins, sum(input_tokens) AS tin, sum(output_tokens) AS tout,"
        " sum(cache_write_tokens) AS tcw, sum(cache_read_tokens) AS tcr, count(*) FILTER (WHERE NOT priced) AS unpriced"
        " FROM llm_usage WHERE created_at >= $1 AND created_at < $2 GROUP BY provider ORDER BY provider",
        start, end,
    )
    lines = [f"Расход LLM-шлюза за {day:%d.%m.%Y} ({get_settings().tz}):"]
    total = Decimal(0)
    for r in rows:
        total += r["coins"]
        usd = r["coins"] / 1000
        lines.append(f"- {r['provider']}: {r['coins']:.1f} коинов (${usd:.2f}), запросов {r['n']}, токены: вход {r['tin']},"
                     f" выход {r['tout']}, кэш запись {r['tcw']}, кэш чтение {r['tcr']}"
                     + (f"; без цены в таблице: {r['unpriced']}" if r["unpriced"] else ""))
    if not rows:
        lines.append("- запросов не было")
    lines.append(f"Итого: {total:.1f} коинов (${total / 1000:.2f}). Сверьте с консолями провайдеров.")
    models = await pool.fetch(
        "SELECT DISTINCT provider, model FROM llm_usage WHERE created_at >= $1 AND created_at < $2 AND NOT priced",
        start, end,
    )
    if models:
        lines.append("Модели без цены (добавьте в NOX_MODEL_PRICES): "
                     + ", ".join(f"{m['provider']}/{m['model']}" for m in models))
    text = "\n".join(lines)
    await alerts.send(f"Nox LLM-шлюз: сводка за {day:%d.%m.%Y}", text)
    return text


async def maybe_daily_summary() -> bool:
    s = get_settings()
    now = datetime.now(ZoneInfo(s.tz))
    if now.hour < s.daily_summary_hour:
        return False
    yesterday = now.date() - timedelta(days=1)
    pool = get_pool()
    if await _state_get(pool, "daily_summary") == yesterday.isoformat():
        return False
    await _state_set(pool, "daily_summary", yesterday.isoformat())
    await daily_summary(yesterday)
    return True


# ---- loop -----------------------------------------------------------------------------------


async def tick() -> dict:
    """One pass of every billing step; skipped when another runner holds the lock."""
    pool = get_pool()
    async with pool.acquire() as conn:
        if not await conn.fetchval(f"SELECT pg_try_advisory_lock({_LOCK})"):
            return {"skipped": True}
        try:
            out = {
                "renewal_notices": await renewal_notices(),
                "renewed": await rollover_due(),
                "suspended": await suspend_overdue(),
                "trials_ended": await suspend_trials(),
                "low_balance": await low_balance_notices(),
                "summary": await maybe_daily_summary(),
            }
            await wallet.prune_reservations(conn)
            return out
        finally:
            await conn.execute(f"SELECT pg_advisory_unlock({_LOCK})")


async def loop() -> None:
    interval = get_settings().billing_tick_seconds
    while True:
        try:
            result = await tick()
            if any(v for k, v in result.items() if k != "summary"):
                log.info("billing: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("billing tick failed")
        await asyncio.sleep(interval)
