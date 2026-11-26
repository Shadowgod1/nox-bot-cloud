"""Provisioning job state machine.

  account:  verified --approve--> approved --claim--> provisioning --ok--> active
                                                                  --fail-> failed --retry--> approved
            active --suspend job ok--> suspended --resume job ok--> active
            active|suspended|failed --remove job ok--> removed
  rekey (LLM gateway key): the instance .env gets the job's new virtual key; on success every other key of the
            account is revoked, on failure the new key is revoked (the old one keeps working)
  job:      pending --claim--> claimed --result--> done | failed
            claimed for longer than NOX_JOB_TIMEOUT_MINUTES --> failed (timeout)

At most one open (pending / claimed) job per account (partial unique index jobs_one_open_per_account).
"""

import logging
import re

import asyncpg

from nox_portal import letters, mailer, security, wallet
from nox_portal.config import get_settings
from nox_portal.deps import audit
from nox_portal.errors import ApiError

log = logging.getLogger("nox_portal.jobs")

TIMEOUT_ERROR = "нет ответа от провизионера (таймаут); проверьте journalctl -u nox-provisioner и повторите"


# what argon2-cffi's PasswordHasher writes (security.hash_password); anything else is not handed to the instance
_ARGON2ID_RE = re.compile(r"^\$argon2id\$v=\d+\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}$")


async def owner_for(conn: asyncpg.Connection, job: asyncpg.Record) -> dict:
    """Owner of a new instance (create job): e-mail, contact name, company and the cabinet password hash, so the
    client logs in to the panel with the cabinet password. Read at claim time: a password reset before the bot is
    created is taken into account. Empty when the hash is not an argon2id one (then the setup link is used)."""
    row = await conn.fetchrow("SELECT email, contact_name, company, password_hash FROM accounts WHERE id = $1",
                              job["account_id"])
    if row is None or not _ARGON2ID_RE.match(row["password_hash"] or ""):
        return {}
    return {"owner_email": row["email"].strip().lower(), "owner_name": row["contact_name"], "company": row["company"],
            "owner_password_hash": row["password_hash"]}


def public_url_for(slug: str) -> str:
    return f"https://{slug}.{get_settings().instance_domain}"


async def enqueue(conn: asyncpg.Connection, account_id: int, kind: str, slug: str, *, coins: int | None = None,
                  llm_key_sealed: str | None = None, staff_id: int | None = None,
                  gateway_key_sealed: str | None = None, key_id: int | None = None) -> int:
    try:
        return await conn.fetchval(
            "INSERT INTO jobs(account_id, kind, slug, coins, llm_key_sealed, created_by, gateway_key_sealed, key_id)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
            account_id, kind, slug, coins, llm_key_sealed, staff_id, gateway_key_sealed, key_id,
        )
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "job_in_progress", "Для этой заявки уже выполняется операция, дождитесь её завершения") \
            from None


async def expire_stale(conn: asyncpg.Connection) -> int:
    timeout = get_settings().job_timeout_minutes
    rows = await conn.fetch(
        "UPDATE jobs SET status = 'failed', error = $2, finished_at = now()"
        " WHERE status = 'claimed' AND claimed_at < now() - make_interval(mins => $1)"
        " RETURNING id, account_id, kind, key_id",
        timeout, TIMEOUT_ERROR,
    )
    for r in rows:
        if r["kind"] == "rekey" and r["key_id"]:
            await wallet.revoke_keys(conn, r["account_id"], "rekey job timed out", only=r["key_id"])
            await conn.execute("UPDATE jobs SET gateway_key_sealed = NULL WHERE id = $1", r["id"])
        if r["kind"] == "create":
            await conn.execute(
                "UPDATE accounts SET status = 'failed', last_error = $2, updated_at = now()"
                " WHERE id = $1 AND status = 'provisioning'",
                r["account_id"], TIMEOUT_ERROR,
            )
        else:
            await conn.execute("UPDATE accounts SET last_error = $2, updated_at = now() WHERE id = $1",
                               r["account_id"], TIMEOUT_ERROR)
        await audit(conn, f"provisioner.{r['kind']}.timeout", account_id=r["account_id"], details={"job": r["id"]})
    return len(rows)


async def claim(conn: asyncpg.Connection) -> asyncpg.Record | None:
    async with conn.transaction():
        await expire_stale(conn)
        job = await conn.fetchrow(
            "SELECT id, account_id, kind, slug, coins, llm_key_sealed, gateway_key_sealed FROM jobs WHERE status = 'pending'"
            " ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED"
        )
        if job is None:
            return None
        await conn.execute("UPDATE jobs SET status = 'claimed', claimed_at = now() WHERE id = $1", job["id"])
        if job["kind"] == "create":
            await conn.execute("UPDATE accounts SET status = 'provisioning', updated_at = now() WHERE id = $1",
                               job["account_id"])
        await audit(conn, f"provisioner.{job['kind']}.claimed", account_id=job["account_id"],
                    details={"job": job["id"], "slug": job["slug"]})
        return job


def _check_urls(slug: str, admin_url: str | None, setup_url: str | None) -> None:
    expected = public_url_for(slug)
    if admin_url is not None and admin_url.rstrip("/") != expected:
        raise ApiError(422, "bad_result", f"admin_url must be {expected}")
    if setup_url is not None and not setup_url.startswith(expected + "/setup?token="):
        raise ApiError(422, "bad_result", "setup_url must be <admin_url>/setup?token=...")


async def finish(conn: asyncpg.Connection, job_id: int, ok: bool, *, port: int | None, admin_url: str | None,
                 setup_url: str | None, error: str | None, owner_preset: bool = False) -> None:
    """Record the provisioner's result; e-mails the client after the transaction commits."""
    notify: tuple[str, tuple[str, str]] | None = None
    staff_alert: tuple[str, str] | None = None  # a failed job: staff must know, nothing retries it
    async with conn.transaction():
        job = await conn.fetchrow(
            "SELECT j.id, j.account_id, j.kind, j.slug, j.status, j.key_id, a.email, a.contact_name, a.admin_url"
            " FROM jobs j JOIN accounts a ON a.id = j.account_id WHERE j.id = $1 FOR UPDATE OF j",
            job_id,
        )
        if job is None:
            raise ApiError(404, "not_found", "job not found")
        if job["status"] != "claimed":
            raise ApiError(409, "job_not_claimed", f"job is {job['status']}, not claimed")
        kind, account_id = job["kind"], job["account_id"]
        if ok:
            _check_urls(job["slug"], admin_url, setup_url)
            await conn.execute(
                "UPDATE jobs SET status = 'done', error = NULL, llm_key_sealed = NULL, gateway_key_sealed = NULL,"
                " finished_at = now() WHERE id = $1",
                job_id,
            )
            if kind == "create":
                if not admin_url or not port:
                    raise ApiError(422, "bad_result", "create result needs port and admin_url")
                # the owner came from the application: the panel login is the cabinet one, no setup link
                if owner_preset:
                    setup_url = None
                await conn.execute(
                    "UPDATE accounts SET status = 'active', port = $2, admin_url = $3, setup_link_enc = $4,"
                    " owner_preset = $5, last_error = NULL, updated_at = now() WHERE id = $1",
                    account_id, port, admin_url.rstrip("/"),
                    security.encrypt(setup_url) if setup_url else None, owner_preset,
                )
                notify = (job["email"], letters.bot_ready(job["contact_name"], admin_url.rstrip("/"), setup_url,
                                                          owner_email=job["email"] if owner_preset else None))
            elif kind == "suspend":
                await conn.execute(
                    "UPDATE accounts SET status = 'suspended', last_error = NULL, updated_at = now() WHERE id = $1",
                    account_id,
                )
                notify = (job["email"], letters.suspended(job["contact_name"]))
            elif kind == "resume":
                await conn.execute(
                    "UPDATE accounts SET status = 'active', last_error = NULL, updated_at = now() WHERE id = $1",
                    account_id,
                )
                notify = (job["email"], letters.resumed(job["contact_name"], job["admin_url"] or ""))
            elif kind == "remove":
                await conn.execute(
                    "UPDATE accounts SET status = 'removed', port = NULL, admin_url = NULL, setup_link_enc = NULL,"
                    " owner_preset = false, last_error = NULL, updated_at = now() WHERE id = $1",
                    account_id,
                )
                await conn.execute("UPDATE jobs SET llm_key_sealed = NULL, gateway_key_sealed = NULL"
                                   " WHERE account_id = $1", account_id)
                await wallet.revoke_keys(conn, account_id, "bot removed")
            elif kind == "rekey" and job["key_id"]:
                # the new key is in the instance: the older ones stop working now
                await wallet.revoke_keys(conn, account_id, "replaced by a reissued key", keep=job["key_id"])
            await audit(conn, f"provisioner.{kind}.done", account_id=account_id,
                        details={"job": job_id, "port": port, "owner_preset": owner_preset} if kind == "create"
                        else {"job": job_id})
        else:
            clean = security.sanitize(error or "неизвестная ошибка")
            await conn.execute(
                "UPDATE jobs SET status = 'failed', error = $2, finished_at = now() WHERE id = $1", job_id, clean
            )
            if kind == "rekey" and job["key_id"]:
                # never deployed: drop it, the old key keeps working
                await wallet.revoke_keys(conn, account_id, "rekey job failed", only=job["key_id"])
                await conn.execute("UPDATE jobs SET gateway_key_sealed = NULL WHERE id = $1", job_id)
            if kind == "create":
                await conn.execute(
                    "UPDATE accounts SET status = 'failed', last_error = $2, updated_at = now() WHERE id = $1",
                    account_id, clean,
                )
            else:
                await conn.execute("UPDATE accounts SET last_error = $2, updated_at = now() WHERE id = $1",
                                   account_id, clean)
            await audit(conn, f"provisioner.{kind}.failed", account_id=account_id, details={"job": job_id})
            staff_alert = (
                f"Nox Bot: не выполнено «{kind}» для {job['slug']}",
                f"Провижинер не смог выполнить задание {kind} (#{job_id}) для {job['slug']} ({job['email']}).\n"
                f"Ошибка: {clean}\n\nКлиент видит в кабинете «Создание бота задерживается». "
                "Проверьте журнал провижинера и запустите задание заново из карточки заявки.",
            )
    if notify is not None:
        await mailer.send_quiet(notify[0], *notify[1])
    if staff_alert is not None:
        for to in mailer.staff_recipients():
            await mailer.send_quiet(to, *staff_alert)
