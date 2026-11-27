"""Virtual keys, billing periods and the coin balance of an account.

  balance = monthly grant of the current period + topup_carry + top-ups/adjustments since counted_since
            - spend since counted_since

Concurrency (reserve-then-settle): admission locks the account row, computes the balance minus the coins
reserved by requests still in flight, refuses at <= 0 and reserves an upper-bound estimate of the new
request; settle (under the same row lock) writes the ledger row and drops the reservation. Every admitted
request saw a positive balance after all in-flight reservations, so parallel requests can overspend by at
most one request (the last admitted one, if it costs more than what was left).
"""

import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import asyncpg

from nox_portal import security
from nox_portal.errors import ApiError

log = logging.getLogger("nox_portal.wallet")

KEY_PREFIX = "nox_vk_"
KEY_RE = re.compile(r"^nox_vk_[A-Za-z0-9_-]{20,128}$")
DISPLAY_LEN = len(KEY_PREFIX) + 6
# account statuses whose keys work
LIVE_STATUSES = ("approved", "provisioning", "active")
# a reservation older than this is ignored (request crashed before settling) and pruned
RESERVATION_TTL = "15 minutes"


# ---- keys -----------------------------------------------------------------------------------


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def display_prefix(key: str) -> str:
    return key[:DISPLAY_LEN]


async def issue_key(conn: asyncpg.Connection, account_id: int, staff_id: int | None) -> tuple[str, int]:
    """New active key; returns (full key, id). The full key exists only in the caller's hands."""
    key = new_key()
    key_id = await conn.fetchval(
        "INSERT INTO virtual_keys(account_id, key_hash, prefix, created_by) VALUES ($1, $2, $3, $4) RETURNING id",
        account_id, key_hash(key), display_prefix(key), staff_id,
    )
    return key, key_id


def seal_key(key: str) -> str:
    try:
        return security.seal_for_provisioner(key)
    except security.SealingUnavailable:
        raise ApiError(503, "sealing_unavailable",
                       "Нет открытого ключа провизионера: ключ шлюза нельзя передать на сервер") from None


async def revoke_keys(conn: asyncpg.Connection, account_id: int, reason: str, *, keep: int | None = None,
                      only: int | None = None) -> int:
    """Revoke active keys of an account (all, all but `keep`, or just `only`). Returns how many."""
    rows = await conn.fetch(
        "UPDATE virtual_keys SET status = 'revoked', revoked_at = now(), revoke_reason = $2"
        " WHERE account_id = $1 AND status = 'active' AND ($3::bigint IS NULL OR id <> $3)"
        " AND ($4::bigint IS NULL OR id = $4) RETURNING id",
        account_id, reason, keep, only,
    )
    return len(rows)


@dataclass
class KeyOwner:
    key_id: int
    key_status: str
    account_id: int
    account_status: str

    @property
    def live(self) -> bool:
        return self.key_status == "active" and self.account_status in LIVE_STATUSES


async def lookup(conn: asyncpg.Pool | asyncpg.Connection, key: str) -> KeyOwner | None:
    if not KEY_RE.match(key):
        return None
    digest = key_hash(key)
    row = await conn.fetchrow(
        "SELECT k.id, k.status, k.key_hash, a.id AS account_id, a.status AS account_status"
        " FROM virtual_keys k JOIN accounts a ON a.id = k.account_id WHERE k.key_hash = $1",
        digest,
    )
    if row is None or not hmac.compare_digest(row["key_hash"], digest):
        return None
    return KeyOwner(row["id"], row["status"], row["account_id"], row["account_status"])


# ---- periods and grants ---------------------------------------------------------------------


async def start_period(conn: asyncpg.Connection, account_id: int, monthly_coins: int, *,
                       staff_id: int | None, note: str) -> None:
    """Activation: the first period starts now and the monthly coins are granted (caller holds the lock)."""
    row = await conn.fetchrow(
        "UPDATE accounts SET period_start = clock_timestamp(), period_end = clock_timestamp() + interval '1 month',"
        " counted_since = clock_timestamp(), low_balance_sent = 100, updated_at = now() WHERE id = $1"
        " RETURNING period_start",
        account_id,
    )
    if monthly_coins > 0:
        await conn.execute(
            "INSERT INTO coin_grants(account_id, kind, coins, period_start, staff_id, note)"
            " VALUES ($1, 'monthly', $2, $3, $4, $5)",
            account_id, monthly_coins, row["period_start"], staff_id, note,
        )


@dataclass
class Balance:
    status: str
    monthly_limit: int
    period_start: datetime | None
    period_end: datetime | None
    monthly: Decimal
    carry: Decimal
    topups: Decimal
    spent: Decimal
    reserved: Decimal
    # Free (test) tariff: when the trial ends and the account is suspended (accounts.free_until); None otherwise
    trial_end: datetime | None = None

    @property
    def total(self) -> Decimal:
        return self.monthly + self.carry + self.topups

    @property
    def left(self) -> Decimal:
        return self.total - self.spent

    @property
    def available(self) -> Decimal:
        return self.left - self.reserved

    @property
    def coins_left(self) -> int:
        return max(int(self.left), 0)


_BALANCE_SQL = f"""
SELECT a.status, a.coins_limit, a.period_start, a.period_end, a.topup_carry,
  CASE WHEN a.tariff = 'free' THEN a.free_until END AS trial_end,
  coalesce((SELECT sum(coins) FROM coin_grants g WHERE g.account_id = a.id AND g.kind = 'monthly'
            AND g.period_start = a.period_start), 0) AS monthly,
  coalesce((SELECT sum(coins) FROM coin_grants g WHERE g.account_id = a.id AND g.kind <> 'monthly'
            AND g.created_at >= a.counted_since), 0) AS topups,
  coalesce((SELECT sum(coins) FROM llm_usage u WHERE u.account_id = a.id AND u.created_at >= a.counted_since), 0)
    AS spent,
  coalesce((SELECT sum(coins) FROM llm_reservations r WHERE r.account_id = a.id
            AND r.created_at > clock_timestamp() - interval '{RESERVATION_TTL}'), 0) AS reserved
FROM accounts a WHERE a.id = $1
"""


def _balance(row: asyncpg.Record) -> Balance:
    return Balance(status=row["status"], monthly_limit=row["coins_limit"] or 0, period_start=row["period_start"],
                   period_end=row["period_end"], monthly=row["monthly"], carry=row["topup_carry"],
                   topups=row["topups"], spent=row["spent"], reserved=row["reserved"], trial_end=row["trial_end"])


async def balance(conn: asyncpg.Pool | asyncpg.Connection, account_id: int, *, lock: bool = False) -> Balance:
    row = await conn.fetchrow(_BALANCE_SQL + (" FOR UPDATE OF a" if lock else ""), account_id)
    if row is None:
        raise ApiError(404, "not_found", "account not found")
    return _balance(row)


async def admit(pool: asyncpg.Pool, account_id: int, estimate: Decimal) -> int | None:
    """Reserve coins for one request. Returns the reservation id, or None when the balance is used up."""
    async with pool.acquire() as conn, conn.transaction():
        bal = await balance(conn, account_id, lock=True)
        if bal.period_start is None or bal.available <= 0:
            return None
        return await conn.fetchval("INSERT INTO llm_reservations(account_id, coins) VALUES ($1, $2) RETURNING id",
                                   account_id, max(estimate, Decimal(0)))


async def release(pool: asyncpg.Pool, reservation_id: int | None) -> None:
    if reservation_id is not None:
        await pool.execute("DELETE FROM llm_reservations WHERE id = $1", reservation_id)


@dataclass
class Charge:
    account_id: int
    key_id: int
    provider: str
    model: str
    endpoint: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    coins: Decimal
    priced: bool
    status: int | None
    request_id: str
    upstream_request_id: str | None


async def settle(pool: asyncpg.Pool, reservation_id: int | None, charge: Charge | None) -> None:
    """Write the ledger row (if any) and drop the reservation, atomically under the account lock."""
    if charge is None:
        await release(pool, reservation_id)
        return
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT 1 FROM accounts WHERE id = $1 FOR UPDATE", charge.account_id)
        # created_at = clock_timestamp() after the lock: ordered consistently with period rollovers
        await conn.execute(
            "INSERT INTO llm_usage(account_id, key_id, provider, model, endpoint, input_tokens, output_tokens,"
            " cache_write_tokens, cache_read_tokens, coins, priced, status, request_id, upstream_request_id,"
            " created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14, clock_timestamp())",
            charge.account_id, charge.key_id, charge.provider, charge.model[:200], charge.endpoint[:200],
            charge.input_tokens, charge.output_tokens, charge.cache_write_tokens, charge.cache_read_tokens,
            charge.coins, charge.priced, charge.status, charge.request_id,
            (charge.upstream_request_id or "")[:200] or None,
        )
        if reservation_id is not None:
            await conn.execute("DELETE FROM llm_reservations WHERE id = $1", reservation_id)
        await conn.execute(
            "UPDATE virtual_keys SET last_used_at = now() WHERE id = $1"
            " AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
            charge.key_id,
        )


async def prune_reservations(conn: asyncpg.Pool | asyncpg.Connection) -> None:
    await conn.execute(f"DELETE FROM llm_reservations WHERE created_at < clock_timestamp() - interval '{RESERVATION_TTL}'")


# ---- rollover -------------------------------------------------------------------------------


async def rollover(conn: asyncpg.Connection, account_id: int, *, grace_days: int, monthly_coins: int,
                   note: str) -> dict:
    """Close the current period and open the next one with a fresh monthly grant (caller holds the lock and
    has checked the renewal is paid). Unused monthly coins expire, top-up coins carry over. The next period
    continues from period_end when renewed on time or within the grace days, otherwise it starts now."""
    bal = await balance(conn, account_id, lock=True)
    overflow = max(bal.spent - bal.monthly, Decimal(0))  # spend beyond the monthly coins ate top-ups
    carry = max(bal.carry + bal.topups - overflow, Decimal(0))
    row = await conn.fetchrow(
        "UPDATE accounts SET"
        " period_start = CASE WHEN clock_timestamp() < period_end + make_interval(days => $2) THEN period_end"
        "                     ELSE clock_timestamp() END,"
        " counted_since = clock_timestamp(), topup_carry = $3, low_balance_sent = 100, updated_at = now()"
        " WHERE id = $1 RETURNING period_start",
        account_id, grace_days, carry,
    )
    await conn.execute("UPDATE accounts SET period_end = period_start + interval '1 month' WHERE id = $1", account_id)
    if monthly_coins > 0:
        await conn.execute(
            "INSERT INTO coin_grants(account_id, kind, coins, period_start, note) VALUES ($1, 'monthly', $2, $3, $4)"
            " ON CONFLICT DO NOTHING",
            account_id, monthly_coins, row["period_start"], note,
        )
    return {"spent": str(bal.spent), "monthly": str(bal.monthly), "carry": str(carry),
            "period_start": row["period_start"].isoformat()}
