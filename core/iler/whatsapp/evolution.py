"""Evolution API v2.3.7 client implementing `WhatsAppGateway`.

Rules (spec section 11):
- an instance is never deleted or recreated here; `ensure_instance` only creates a missing one;
- pairing codes are requested only from the `close` state;
- send failures are reported to the connection monitor (a half-open session still says `open`).
"""

import logging
import re
from typing import Literal

import httpx

from iler.config import Settings

log = logging.getLogger("iler.whatsapp")

# MESSAGES_SET / CHATS_SET: the history WhatsApp passes to a freshly linked device; only the chat ids are
# used (reply rule "only new contacts", iler.reply_rules).
WEBHOOK_EVENTS = ["MESSAGES_UPSERT", "CONNECTION_UPDATE", "MESSAGES_SET", "CHATS_SET"]


class EvolutionError(Exception):
    """Evolution call failed (transport error or non-2xx answer)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class SendUncertain(EvolutionError):
    """The request may have reached Evolution (read timeout / dropped connection): the message may be sent."""


class WrongState(EvolutionError):
    """The operation is not allowed in the current connection state."""

    def __init__(self, state: str):
        super().__init__(f"not allowed in state {state}")
        self.state = state


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _is_session_failure(status: int, body: str) -> bool:
    """Whether a failed send points at a broken WhatsApp session (vs. a bad request)."""
    if status >= 500:
        return True
    low = body.lower()
    return "connection closed" in low or "not connected" in low


class EvolutionGateway:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 15.0):
        self.settings = settings
        self.instance = settings.evo_instance
        self._transport = transport
        self._timeout = httpx.Timeout(timeout, connect=5.0)
        self._client: httpx.AsyncClient | None = None

    # ---------- plumbing ----------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.settings.evo_url.rstrip("/"),
                headers={"apikey": self.settings.evo_api_key},
                timeout=self._timeout,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, *, ok404: bool = False, timeout: float | None = None,
                       **kwargs) -> httpx.Response:
        try:
            r = await self._http().request(
                method, path, timeout=httpx.Timeout(timeout, connect=5.0) if timeout else httpx.USE_CLIENT_DEFAULT,
                **kwargs,
            )
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError) as e:
            raise SendUncertain(f"evolution {method} {path}: {type(e).__name__}") from e
        except httpx.HTTPError as e:
            raise EvolutionError(f"evolution unreachable: {type(e).__name__}") from e
        if r.status_code == 404 and ok404:
            return r
        if r.status_code >= 400:
            raise EvolutionError(f"evolution {method} {path} -> {r.status_code}: {r.text[:300]}", r.status_code)
        return r

    # ---------- WhatsAppGateway ----------

    async def send_text(self, jid: str, text: str) -> str:
        from iler.whatsapp import monitor  # local import: monitor needs the DB, the client does not

        try:
            r = await self._request("POST", f"/message/sendText/{self.instance}", json={"number": jid, "text": text})
        except EvolutionError as e:
            body = str(e)
            session_broken = (
                e.status is None  # transport level: timeout, refused, dropped
                or _is_session_failure(e.status, body)
            )
            if session_broken:
                await monitor.safe_record_send_failure(body)
            raise
        await monitor.safe_record_send_success()
        try:
            return str(r.json()["key"]["id"])
        except (ValueError, KeyError, TypeError) as e:
            raise SendUncertain(f"unexpected sendText answer: {r.text[:200]}") from e

    async def send_presence(self, jid: str, state: Literal["composing", "paused"], ms: int) -> None:
        # Evolution holds the request for `delay` ms; presence is cosmetic, so failures are only logged.
        try:
            await self._request(
                "POST", f"/chat/sendPresence/{self.instance}",
                json={"number": jid, "presence": state, "delay": int(ms)},
                timeout=ms / 1000 + 10,
            )
        except EvolutionError as e:
            log.warning("presence failed: %s", e)

    async def _state_or_missing(self) -> str:
        r = await self._request("GET", f"/instance/connectionState/{self.instance}", ok404=True)
        if r.status_code == 404:
            return "missing"
        data = r.json()
        inst = data.get("instance") if isinstance(data, dict) else None
        state = (inst or {}).get("state") if isinstance(inst, dict) else data.get("state")
        return str(state or "close")

    async def connection_state(self) -> str:
        state = await self._state_or_missing()
        return "close" if state == "missing" else state

    async def ensure_instance(self, webhook_url: str) -> None:
        if await self._state_or_missing() == "missing":
            await self._request(
                "POST", "/instance/create",
                json={"instanceName": self.instance, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
            )
        await self._request(
            "POST", f"/webhook/set/{self.instance}",
            json={
                "webhook": {
                    "enabled": True,
                    "url": webhook_url,
                    "headers": {"apikey": self.settings.evo_api_key},
                    "byEvents": False,
                    "base64": False,
                    "events": WEBHOOK_EVENTS,
                }
            },
        )

    async def connect(self) -> dict:
        state = await self._state_or_missing()
        if state == "open":
            raise WrongState(state)
        if state == "missing":
            await self.ensure_instance(self.settings.webhook_url)
        r = await self._request("GET", f"/instance/connect/{self.instance}")
        data = r.json() if r.content else {}
        qr = data.get("base64") or (data.get("qrcode") or {}).get("base64")
        return {"qr_base64": qr} if qr else {}

    async def pairing_code(self, phone: str) -> str:
        number = _digits(phone)
        state = await self._state_or_missing()
        if state not in ("close", "missing"):
            raise WrongState(state)
        if state == "missing":
            await self.ensure_instance(self.settings.webhook_url)
        r = await self._request("GET", f"/instance/connect/{self.instance}", params={"number": number})
        code = (r.json() or {}).get("pairingCode")
        if not code:
            raise EvolutionError("evolution returned no pairing code")
        return str(code)

    async def logout(self) -> None:
        try:
            await self._request("DELETE", f"/instance/logout/{self.instance}")
        except EvolutionError as e:
            # 400/404: already logged out or no instance — the goal is reached.
            if e.status not in (400, 404):
                raise
