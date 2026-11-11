"""WhatsApp connection state (table `wa_instance`, single row id=1) and its monitor.

Statuses: close | connecting | open | degraded. `degraded` means Evolution reports `open` but sends
keep failing (half-open session, spec 5.2 / 11.4); it is cleared by a successful send or a fresh
CONNECTION_UPDATE `open`. When the channel has been down for longer than 5 minutes an alert is
published (`whatsapp.status` with `alert: true`) and e-mailed if SMTP is configured.
"""

import asyncio
import logging
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from iler import events, settings_store
from iler.config import get_settings
from iler.db.pool import get_pool
from iler.settings_models import NotifySettings

log = logging.getLogger("iler.whatsapp.monitor")

SEND_FAILURE_THRESHOLD = 3
DOWN_ALERT_AFTER = timedelta(minutes=5)

_KEEP = object()
_alerted = False


def _public(row) -> dict:
    return {"status": row["status"], "phone": row["phone"], "last_error": row["last_error"]}


async def _ensure_row(conn) -> None:
    await conn.execute(
        "INSERT INTO wa_instance(id, evo_name) VALUES (1, $1) ON CONFLICT (id) DO NOTHING",
        get_settings().evo_instance,
    )


async def get_status() -> dict:
    row = await get_pool().fetchrow("SELECT status, phone, last_error FROM wa_instance WHERE id = 1")
    if row is None:
        return {"status": "close", "phone": None, "last_error": None}
    return _public(row)


async def set_status(status: str, *, phone=_KEEP, last_error=_KEEP, reset_failures: bool = False,
                     seen: bool = False) -> dict:
    """Update the row and publish `whatsapp.status` when the visible state changed."""
    async with get_pool().acquire() as conn, conn.transaction():
        await _ensure_row(conn)
        old = await conn.fetchrow("SELECT status, phone, last_error FROM wa_instance WHERE id = 1 FOR UPDATE")
        new = await conn.fetchrow(
            "UPDATE wa_instance SET status = $1,"
            " phone = CASE WHEN $2 THEN $3 ELSE phone END,"
            " last_error = CASE WHEN $4 THEN $5 ELSE last_error END,"
            " send_failures = CASE WHEN $6 THEN 0 ELSE send_failures END,"
            " last_seen_at = CASE WHEN $7 THEN now() ELSE last_seen_at END,"
            " updated_at = now()"
            " WHERE id = 1 RETURNING status, phone, last_error",
            status,
            phone is not _KEEP, None if phone is _KEEP else phone,
            last_error is not _KEEP, None if last_error is _KEEP else (last_error or None),
            reset_failures,
            seen,
        )
    if _public(old) != _public(new):
        events.publish("whatsapp.status", _public(new))
    return _public(new)


async def record_send_failure(error: str) -> None:
    """Count a failed send; at the threshold an `open` session becomes `degraded`."""
    async with get_pool().acquire() as conn:
        await _ensure_row(conn)
        row = await conn.fetchrow(
            "UPDATE wa_instance SET send_failures = send_failures + 1, last_error = $1, updated_at = now()"
            " WHERE id = 1 RETURNING send_failures, status",
            (error or "send failed")[:500],
        )
    if row["send_failures"] >= SEND_FAILURE_THRESHOLD and row["status"] == "open":
        log.warning("WhatsApp sends failing while state is open: marking degraded")
        await set_status("degraded")


async def record_send_success() -> None:
    row = await get_pool().fetchrow(
        "UPDATE wa_instance SET send_failures = 0, last_seen_at = now(), updated_at = now(),"
        " last_error = CASE WHEN send_failures > 0 OR status = 'degraded' THEN NULL ELSE last_error END"
        " WHERE id = 1 RETURNING status"
    )
    if row is not None and row["status"] == "degraded":
        await set_status("open", last_error=None)


async def safe_record_send_failure(error: str) -> None:
    try:
        await record_send_failure(error)
    except Exception:  # noqa: BLE001 - bookkeeping must never break a send path
        log.exception("could not record send failure")


async def safe_record_send_success() -> None:
    try:
        await record_send_success()
    except Exception:  # noqa: BLE001
        log.exception("could not record send success")


async def apply_connection_update(state: str, *, wuid: str | None = None, status_reason=None) -> dict:
    """CONNECTION_UPDATE from Evolution (data.instance is a string in v2)."""
    state = state if state in ("open", "close", "connecting") else "close"
    if state == "open":
        phone = _digits(wuid) if wuid else _KEEP
        return await set_status("open", phone=phone, last_error=None, reset_failures=True, seen=True)
    error = "logged_out" if str(status_reason) == "401" else _KEEP
    return await set_status(state, last_error=error)


def _digits(jid: str) -> str:
    return "".join(ch for ch in jid.split("@", 1)[0].split(":", 1)[0] if ch.isdigit())


async def check_once(gateway) -> dict:
    """One monitor tick: poll connectionState, keep `degraded` while sends fail, alert on long outages."""
    global _alerted
    try:
        state = await gateway.connection_state()
        error = _KEEP
    except Exception as e:  # noqa: BLE001
        log.warning("connectionState failed: %s", e)
        msg = str(e)
        state, error = "close", (msg if "unreachable" in msg else f"evolution unreachable: {msg}")[:500]

    row = await get_pool().fetchrow("SELECT status, send_failures FROM wa_instance WHERE id = 1")
    if state == "open":
        if row is not None and row["status"] == "degraded" and row["send_failures"] >= SEND_FAILURE_THRESHOLD:
            current = await set_status("degraded")
        else:
            current = await set_status("open", seen=True)
    else:
        current = await set_status(state if state in ("close", "connecting") else "close", last_error=error)

    if current["status"] == "open":
        _alerted = False
        return current

    last_seen = await get_pool().fetchval("SELECT last_seen_at FROM wa_instance WHERE id = 1")
    if last_seen is None or _alerted:
        return current  # never connected (setup not finished) or already alerted
    down_for = datetime.now(UTC) - last_seen
    if down_for >= DOWN_ALERT_AFTER:
        _alerted = True
        minutes = int(down_for.total_seconds() // 60)
        events.publish("whatsapp.status", {**current, "alert": True, "down_minutes": minutes})
        try:
            await send_alert(f"WhatsApp is not connected for {minutes} min (status: {current['status']}).")
        except Exception:  # noqa: BLE001
            log.exception("alert delivery failed")
    return current


async def send_alert(text: str) -> None:
    """Notify the admin through a channel other than WhatsApp (SMTP), if configured."""
    notify = await settings_store.get("notify", NotifySettings)
    smtp = notify.smtp
    if smtp is None or not smtp.host or not smtp.to:
        return
    msg = EmailMessage()
    msg["Subject"] = "ILER Bot: WhatsApp disconnected"
    msg["From"] = smtp.sender or smtp.user
    msg["To"] = ", ".join(smtp.to)
    msg.set_content(text)

    def _send() -> None:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=15) as s:
            if smtp.starttls:
                s.starttls()
            if smtp.user:
                s.login(smtp.user, smtp.password.get_secret_value() if smtp.password else "")
            s.send_message(msg)

    await asyncio.to_thread(_send)


def reset_alert_state() -> None:
    global _alerted
    _alerted = False
