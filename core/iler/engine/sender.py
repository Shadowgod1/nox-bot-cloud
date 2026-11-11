"""Outbound WhatsApp path (spec 5.1 step 8).

Every outgoing text is an `outbound_intents` row with a unique idempotency key:
- bot reply parts:     "{conversation_id}:{turn_id}:{part_index}"
- operator messages:   "op:{conversation_id}:{uuid}"
- booking messages:    "booking:{booking_id}:{kind}"
- admin notifications: "admin:{...}:{target jid}"
An intent is claimed atomically (pending/failed -> sending), so a repeated call with the same key never
sends twice. Outcomes: `sent`; `uncertain` (read timeout: the message may have gone out, never resent
automatically); `failed` (definitely not sent; the reconcile worker retries it a few times).
EvolutionGateway.send_text reports send health to the WhatsApp monitor itself.
"""

import logging
import re
import uuid

from iler import settings_store
from iler.db.pool import get_pool
from iler.engine import store
from iler.engine.pii import mask_phone_for_log
from iler.settings_models import BotSettings, NotifySettings
from iler.whatsapp import get_gateway
from iler.whatsapp.evolution import SendUncertain

log = logging.getLogger("iler.engine.sender")

SPLIT_MARKER = "[SPLIT]"
MAX_PARTS = 4
PRESENCE_MS_PER_CHAR = 40
PRESENCE_MAX_MS = 4000


class SendFailed(Exception):
    """The message was definitely not delivered to WhatsApp."""


def split_reply(text: str, max_parts: int = MAX_PARTS) -> list[str]:
    """Split on [SPLIT] markers, else on blank lines; the tail beyond `max_parts` stays in the last part."""
    text = (text or "").strip()
    if not text:
        return []
    if SPLIT_MARKER in text:
        parts = [p.strip() for p in text.split(SPLIT_MARKER)]
        sep = "\n"
    else:
        parts = [p.strip() for p in re.split(r"\n\s*\n", text)]
        sep = "\n\n"
    parts = [p for p in parts if p]
    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + [sep.join(parts[max_parts - 1:])]
    return parts


def presence_ms(text: str) -> int:
    return min(len(text) * PRESENCE_MS_PER_CHAR, PRESENCE_MAX_MS)


def _author_for(idem_key: str) -> str:
    return "operator" if idem_key.startswith("op:") else "bot"


async def _deliver(intent_id: int, idem_key: str, conversation_id: int | None, jid: str, text: str,
                   *, presence: bool) -> str:
    """Send a claimed (status=sending) intent and record the outcome."""
    pool = get_pool()
    gw = get_gateway()
    try:
        if presence:
            await gw.send_presence(jid, "composing", presence_ms(text))
        evo_id = await gw.send_text(jid, text)
    except SendUncertain as e:
        status, evo_id, error = "uncertain", None, str(e)
    except Exception as e:  # noqa: BLE001 - EvolutionError or anything else: not delivered
        status, evo_id, error = "failed", None, f"{type(e).__name__}: {e}"
    else:
        status, error = "sent", None
    await pool.execute(
        "UPDATE outbound_intents SET status = $2, evo_message_id = $3, error = $4, updated_at = now() WHERE id = $1",
        intent_id, status, evo_id, error[:1000] if error else None,
    )
    if status != "sent":
        log.warning("send %s to %s: %s", status, mask_phone_for_log(jid), error)
    if conversation_id is not None and status in ("sent", "uncertain"):
        await store.insert_message(conversation_id, "out", _author_for(idem_key), text, evo_message_id=evo_id)
    return status


async def send_text_intent(idem_key: str, conversation_id: int | None, jid: str, text: str,
                           *, presence: bool = True) -> tuple[str, bool]:
    """Send once per idem_key. Returns (status, attempted_now)."""
    pool = get_pool()
    await pool.execute(
        "INSERT INTO outbound_intents(idem_key, conversation_id, chat_jid, text) VALUES ($1, $2, $3, $4)"
        " ON CONFLICT (idem_key) DO NOTHING",
        idem_key, conversation_id, jid, text,
    )
    claimed = await pool.fetchrow(
        "UPDATE outbound_intents SET status = 'sending', attempts = attempts + 1, updated_at = now()"
        " WHERE idem_key = $1 AND status IN ('pending', 'failed') RETURNING id",
        idem_key,
    )
    if claimed is None:
        return await pool.fetchval("SELECT status FROM outbound_intents WHERE idem_key = $1", idem_key), False
    return await _deliver(claimed["id"], idem_key, conversation_id, jid, text, presence=presence), True


async def retry_intent(intent_id: int) -> str | None:
    """Re-send a `failed` intent (reconcile worker). None when it was not claimable."""
    row = await get_pool().fetchrow(
        "UPDATE outbound_intents SET status = 'sending', attempts = attempts + 1, updated_at = now()"
        " WHERE id = $1 AND status = 'failed' RETURNING id, idem_key, conversation_id, chat_jid, text",
        intent_id,
    )
    if row is None:
        return None
    return await _deliver(row["id"], row["idem_key"], row["conversation_id"], row["chat_jid"], row["text"],
                          presence=False)


async def send_reply(conversation_id: int, parts: list[str], *, turn_id: str) -> list[str]:
    """Send bot reply parts in order with typing presence. Stops after a part that failed now (later parts
    would arrive out of context); parts already decided earlier (same idem key) are skipped, not resent."""
    conv = await store.get_conversation(conversation_id)
    if conv is None:
        return []
    statuses: list[str] = []
    for i, part in enumerate(parts):
        status, attempted = await send_text_intent(f"{conversation_id}:{turn_id}:{i}", conversation_id,
                                                   conv["chat_jid"], part)
        statuses.append(status)
        if attempted and status != "sent":
            break
    return statuses


async def send_to_conversation(conversation_id: int, text: str, idem_key: str) -> str:
    """One bot-authored message to the client (booking outcome, timeout follow-up, after-hours text)."""
    conv = await store.get_conversation(conversation_id)
    if conv is None:
        return "failed"
    status, _ = await send_text_intent(idem_key, conversation_id, conv["chat_jid"], text)
    return status


async def send_to_jid(idem_key: str, jid: str, text: str) -> str:
    """A message outside any conversation (admin chats). Not stored in `messages`."""
    status, _ = await send_text_intent(idem_key, None, jid, text, presence=False)
    return status


def admin_targets(notify: NotifySettings) -> list[str]:
    targets: list[str] = []
    for p in notify.admin_phones:
        d = re.sub(r"\D", "", p)
        if d and f"{d}@s.whatsapp.net" not in targets:
            targets.append(f"{d}@s.whatsapp.net")
    if notify.admin_group_jid:
        targets.append(notify.admin_group_jid)
    return targets


async def notify_admins(text: str, *, key: str | None = None) -> list[str]:
    """WhatsApp text to every admin phone and the admin group. Never raises."""
    try:
        notify = await settings_store.get("notify", NotifySettings)
        key = key or uuid.uuid4().hex
        return [await send_to_jid(f"admin:{key}:{jid}", jid, text) for jid in admin_targets(notify)]
    except Exception:  # noqa: BLE001 - a notification must not break the caller
        log.exception("admin notification failed")
        return []


async def send_operator_message(conversation_id: int, text: str, user_name: str) -> None:
    """Operator reply from the admin panel: stored as author 'operator', sent via an outbound intent, and the
    bot is paused for `handover_pause_minutes`. Raises LookupError for an unknown conversation and
    SendFailed when WhatsApp definitely did not take the message (an `uncertain` send does not raise)."""
    conv = await store.get_conversation(conversation_id)
    if conv is None:
        raise LookupError(f"conversation {conversation_id} not found")
    bot = await settings_store.get("bot", BotSettings)
    # Pause first: a client message arriving meanwhile must not trigger a bot turn.
    await store.set_status(conversation_id, "paused", pause_minutes=bot.handover_pause_minutes)
    log.info("operator %s replies in conversation %s", user_name, conversation_id)
    status, _ = await send_text_intent(f"op:{conversation_id}:{uuid.uuid4().hex}", conversation_id,
                                       conv["chat_jid"], text, presence=False)
    if status == "failed":
        raise SendFailed("WhatsApp did not accept the message")
