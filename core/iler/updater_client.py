"""Client for iler-updater (internal HTTP, shared secret).

Contract: GET /version -> {current, available}; POST /update -> {job_id};
GET /update/{job_id} -> {state: queued|backup|pulling|restarting|healthy|rolled_back|failed, log}.
Header: Authorization: Bearer <updater_token>.
"""

from typing import Any
from urllib.parse import quote

import httpx

from iler.config import get_settings
from iler.errors import ApiError

TIMEOUT = httpx.Timeout(10.0, connect=3.0)


class UpdaterClient:
    def __init__(self, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport

    async def _request(self, method: str, path: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=TIMEOUT, transport=self.transport, headers=headers
            ) as c:
                r = await c.request(method, path)
        except httpx.HTTPError as e:
            raise ApiError(503, "updater_unavailable", f"Updater is not reachable ({type(e).__name__})") from e
        if r.status_code == 409:
            raise ApiError(409, "update_in_progress", "An update is already running")
        if r.status_code == 404:
            raise ApiError(404, "not_found", "Unknown update job")
        if r.status_code >= 400:
            raise ApiError(503, "updater_error", f"Updater returned HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ApiError(503, "updater_error", "Updater returned invalid JSON") from e
        if not isinstance(data, dict):
            raise ApiError(503, "updater_error", "Updater returned an unexpected payload")
        return data

    async def get_version(self) -> dict[str, Any]:
        return await self._request("GET", "/version")

    async def start_update(self) -> dict[str, Any]:
        return await self._request("POST", "/update")

    async def update_status(self, job_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/update/{quote(job_id, safe='')}")


def get_updater_client() -> UpdaterClient:
    """FastAPI dependency (overridden in tests)."""
    s = get_settings()
    return UpdaterClient(s.updater_url, s.updater_token)
