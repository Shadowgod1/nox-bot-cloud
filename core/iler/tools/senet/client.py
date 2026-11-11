"""HTTP client for the per-club SENET API: https://{orgname}.api.enes.tech/"""

import hashlib
import re
import time
from collections.abc import Callable
from typing import Any, ClassVar

import httpx

from iler.tools.senet.mapping import extract_items

TIMEOUT_S = 3.0
CACHE_TTL_S = 30.0
ORGNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class SenetError(Exception):
    """Network / HTTP / format error talking to SENET. Never contains the app key."""


def valid_orgname(orgname: str | None) -> bool:
    return bool(orgname) and bool(ORGNAME_RE.match(orgname))


def _default_transport() -> httpx.AsyncBaseTransport | None:
    """Hook for tests (None = real network)."""
    return None


class SenetClient:
    # Shared across instances: the tool creates a client per call.
    _cache: ClassVar[dict[tuple[str, str, str], tuple[float, Any]]] = {}

    def __init__(
        self,
        orgname: str,
        app_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        orgname = (orgname or "").strip().lower()
        if not valid_orgname(orgname):
            raise SenetError("invalid orgname")
        if not app_key:
            raise SenetError("app key is not set")
        self.orgname = orgname
        self._key = app_key
        self._key_id = hashlib.sha256(app_key.encode()).hexdigest()[:16]
        self._transport = transport if transport is not None else _default_transport()
        self._clock = clock

    @property
    def base_url(self) -> str:
        return f"https://{self.orgname}.api.enes.tech"

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    async def get(self, path: str, *, use_cache: bool = True) -> Any:
        ckey = (self.orgname, self._key_id, path)
        now = self._clock()
        if use_cache:
            hit = self._cache.get(ckey)
            if hit and now - hit[0] < CACHE_TTL_S:
                return hit[1]
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=TIMEOUT_S,
                transport=self._transport,
                headers={"Authorization": f"key {self._key}", "Accept": "application/json"},
                follow_redirects=False,
            ) as http:
                resp = await http.get(path)
        except httpx.TimeoutException as e:
            raise SenetError("timeout") from e
        except httpx.HTTPError as e:
            raise SenetError(f"network error: {type(e).__name__}") from e
        if resp.status_code in (401, 403):
            raise SenetError(f"unauthorized (HTTP {resp.status_code}): check the application key")
        if resp.status_code >= 300:
            raise SenetError(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise SenetError("invalid JSON in response") from e
        self._cache[ckey] = (self._clock(), data)
        return data

    async def list_workstations(self, *, use_cache: bool = True) -> list[dict]:
        return extract_items(await self.get("/workstations/", use_cache=use_cache))
