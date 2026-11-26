"""Sliding-window rate limits stored in Postgres (survive restarts, shared by every request)."""

import random
from ipaddress import ip_address, ip_network

from fastapi import Request

from nox_portal.db import get_pool
from nox_portal.errors import ApiError

# Peers allowed to tell us the visitor IP (X-Real-IP set by the portal's nginx, which itself takes it from
# CF-Connecting-IP only when the connection comes from loopback / docker networks).
_TRUSTED = [ip_network(n) for n in ("127.0.0.0/8", "::1/128", "172.16.0.0/12", "192.168.0.0/16")]


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    try:
        trusted = any(ip_address(peer) in n for n in _TRUSTED)
    except ValueError:
        trusted = False
    if trusted:
        forwarded = request.headers.get("x-real-ip", "").strip()
        try:
            return str(ip_address(forwarded))
        except ValueError:
            pass
    return peer or "unknown"


# (limit, window seconds) per bucket
LIMITS: dict[str, tuple[int, int]] = {
    "register:ip": (10, 3600),
    "register:email": (5, 3600),
    "login:ip": (30, 600),
    # above the lockout threshold (10 failures): the lockout answers first, the limit stops floods
    "login:email": (20, 600),
    "verify:ip": (30, 600),
    "send:ip": (10, 3600),
    "send:email": (6, 3600),
    "reset:ip": (30, 600),
    "slug:ip": (60, 600),
    "staff-login:ip": (15, 600),
    "staff-login:email": (20, 600),
    # LLM gateway: requests with a missing / unknown virtual key, per IP
    "llm-badkey:ip": (60, 600),
    # online payment webhooks per IP (providers retry; a flood is cut here and in nginx)
    "payhook:ip": (120, 60),
}


async def hit(bucket: str, value: str) -> None:
    """Count one attempt; raise 429 when the bucket is over its limit."""
    limit, window = LIMITS[bucket]
    key = f"{bucket}:{value.lower()}"
    pool = get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM rate_hits WHERE key = $1 AND at > now() - make_interval(secs => $2)",
            key, window,
        )
        if count >= limit:
            raise ApiError(429, "rate_limited", "Слишком много попыток. Повторите позже.",
                           headers={"Retry-After": str(min(window, 600))})
        await conn.execute("INSERT INTO rate_hits(key) VALUES ($1)", key)
        if random.random() < 0.02:
            await conn.execute("DELETE FROM rate_hits WHERE at < now() - interval '1 day'")
