"""Reply rules: who gets a bot reply (exclusions, "only new contacts") and the "known contacts" memory.

A contact is *known* (it wrote to the number before the bot was linked) when one of these happened:
- `history`: WhatsApp passed the chat to the linked device in the history sync (webhook MESSAGES_SET /
  CHATS_SET), or a message arrived whose WhatsApp timestamp is older than the link time;
- `phone_first`: the first message we ever saw in that chat was written by a person on the phone
  (the owner started the chat themselves: a friend, a supplier, an old client).
With `new_contacts_only` the bot stays silent for known contacts; their messages are still stored and
shown in the admin panel. Conversations the bot already served are not known contacts.
"""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta

from iler.db.pool import get_pool
from iler.settings_models import BotSettings, normalize_phone_digits

log = logging.getLogger("iler.reply_rules")

# WhatsApp timestamps vs. our clock: a message counts as history only when clearly older than the link.
HISTORY_MARGIN = timedelta(minutes=2)


def contact_key(phone: str | None, jid: str | None) -> str:
    """Phone digits when known, else the chat JID (WhatsApp may hide the phone behind an @lid id)."""
    d = normalize_phone_digits(phone) if phone else ""
    if not d and jid and not jid.endswith(("@g.us", "@lid")):
        d = normalize_phone_digits(jid)
    return d or (jid or "")


def is_excluded(phone: str | None, jid: str | None, bot: BotSettings) -> bool:
    if not bot.exclude_phones:
        return False
    keys = {normalize_phone_digits(phone)} if phone else set()
    if jid and not jid.endswith(("@g.us", "@lid")):
        keys.add(normalize_phone_digits(jid))
    keys.discard("")
    return bool(keys & set(bot.exclude_phones))


async def remember(contacts: Iterable[tuple[str | None, str | None]], source: str) -> int:
    """Store known contacts given as (phone, jid) pairs; groups, broadcasts and empty ids are skipped.
    Returns how many new ones were stored."""
    keys: list[str] = []
    for phone, jid in contacts:
        if jid and (jid.endswith(("@g.us", "@broadcast", "@newsletter")) or jid == "status@broadcast"):
            continue
        key = contact_key(phone, jid)
        if key and key not in keys:
            keys.append(key)
    if not keys:
        return 0
    rows = await get_pool().fetch(
        "INSERT INTO known_contacts(contact, source) SELECT unnest($1::text[]), $2"
        " ON CONFLICT (contact) DO NOTHING RETURNING contact",
        keys, source,
    )
    if rows:
        log.info("remembered %d known contact(s) (%s)", len(rows), source)
    return len(rows)


async def is_known(phone: str | None, jid: str | None) -> bool:
    keys = [k for k in {contact_key(phone, jid), jid or ""} if k]
    if not keys:
        return False
    return bool(await get_pool().fetchval(
        "SELECT 1 FROM known_contacts WHERE contact = ANY($1::text[]) LIMIT 1", keys
    ))


async def known_count() -> int:
    return int(await get_pool().fetchval("SELECT count(*) FROM known_contacts") or 0)


async def linked_at() -> datetime | None:
    return await get_pool().fetchval("SELECT linked_at FROM wa_instance WHERE id = 1")


async def is_history(sent_at: datetime | None) -> bool:
    """The message was written before the current WhatsApp link (history delivered to the new device)."""
    if sent_at is None:
        return False
    link = await linked_at()
    return link is not None and sent_at < link - HISTORY_MARGIN


async def skip_reason(conv, bot: BotSettings) -> str | None:
    """Why the bot must not answer this conversation by the reply rules, or None."""
    if is_excluded(conv["phone"], conv["chat_jid"], bot):
        return "excluded"
    if bot.new_contacts_only and await is_known(conv["phone"], conv["chat_jid"]):
        return "known_contact"
    return None
