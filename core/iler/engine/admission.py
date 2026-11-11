"""Who may get a bot reply, and when (spec 5.1 step 4): admins, pause/takeover, working hours."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from iler.settings_models import BotSettings, DayHours, NotifySettings, WeeklyHours
from iler.whatsapp.base import IncomingMessage

log = logging.getLogger("iler.engine.admission")

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_logged_bad_hours: set[str] = set()
_logged_lid: set[str] = set()


def digits(value: str | None) -> str:
    return re.sub(r"\D", "", (value or "").split("@", 1)[0])


def normalize_phone(value: str | None) -> str:
    """Digits only; a KZ/RU local number 8XXXXXXXXXX (11 digits) becomes 7XXXXXXXXXX."""
    d = digits(value)
    if len(d) == 11 and d.startswith("8"):
        d = "7" + d[1:]
    return d


def is_admin(phone_or_jid: str, notify: NotifySettings) -> bool:
    """True for the admin group jid or a (digits-normalized) admin phone / phone jid."""
    if not phone_or_jid:
        return False
    if notify.admin_group_jid and phone_or_jid == notify.admin_group_jid:
        return True
    if phone_or_jid.endswith("@g.us") or phone_or_jid.endswith("@lid"):
        return False
    d = normalize_phone(phone_or_jid)
    return bool(d) and d in {normalize_phone(p) for p in notify.admin_phones if digits(p)}


def is_admin_chat(msg: IncomingMessage, notify: NotifySettings) -> bool:
    """The chat belongs to an admin: the admin group, or a private chat with an admin phone."""
    if msg.is_group:
        return bool(notify.admin_group_jid) and msg.jid == notify.admin_group_jid
    if msg.jid.endswith("@lid") and not msg.phone and msg.jid not in _logged_lid:
        _logged_lid.add(msg.jid)
        log.info("message from %s without a resolvable phone: it cannot be matched to an admin phone", msg.jid)
    return is_admin(msg.jid, notify) or (bool(msg.phone) and is_admin(msg.phone, notify))


class _BadHours(ValueError):
    pass


def _minutes(hhmm: str) -> int:
    """'HH:MM' -> minutes since midnight ('24:00' allowed). Raises _BadHours for anything else."""
    h, _, m = (hhmm or "").strip().partition(":")
    try:
        hours, minutes = int(h), int(m or 0)
    except ValueError:
        raise _BadHours(hhmm) from None
    if hours < 0 or not 0 <= minutes < 60 or hours * 60 + minutes > 24 * 60:
        raise _BadHours(hhmm)
    return hours * 60 + minutes


def _day(hours: WeeklyHours, weekday: int) -> DayHours:
    return getattr(hours, _DAYS[weekday % 7])


def within_hours(hours: WeeklyHours, now_local: datetime) -> bool:
    """`now_local` is a naive or aware datetime in the club's TZ. Overnight shifts (close <= open) spill into
    the next day. Invalid/empty times count as "always open" (logged once) instead of failing every turn."""
    if hours.always_open:
        return True
    try:
        return _within_hours(hours, now_local)
    except _BadHours as e:
        value = str(e)
        if value not in _logged_bad_hours:
            _logged_bad_hours.add(value)
            log.warning("invalid working hours value %r in settings: treating the club as always open", value)
        return True


def _within_hours(hours: WeeklyHours, now_local: datetime) -> bool:
    minute = now_local.hour * 60 + now_local.minute
    today = _day(hours, now_local.weekday())
    if not today.closed:
        o, c = _minutes(today.open), _minutes(today.close)
        if c > o and o <= minute < c:
            return True
        if c <= o and minute >= o:  # overnight shift, evening part
            return True
    yesterday = _day(hours, (now_local - timedelta(days=1)).weekday())
    if not yesterday.closed:
        o, c = _minutes(yesterday.open), _minutes(yesterday.close)
        if c <= o and minute < c:  # overnight shift from yesterday, after midnight
            return True
    return False


def is_paused(status: str, paused_until: datetime | None, now: datetime) -> bool:
    """paused / needs_operator block the bot until `paused_until` (no end time = until the operator releases)."""
    if status not in ("paused", "needs_operator"):
        return False
    return paused_until is None or paused_until > now


@dataclass
class Admission:
    action: Literal["reply", "skip", "fixed_message"]
    reason: str = ""


def decide(conv, bot: BotSettings, now: datetime, now_local: datetime) -> Admission:
    if not bot.enabled:
        return Admission("skip", "bot_disabled")
    if conv["status"] == "closed":
        return Admission("skip", "closed")
    if is_paused(conv["status"], conv["paused_until"], now):
        return Admission("skip", conv["status"])
    if not within_hours(bot.hours, now_local):
        if bot.after_hours_mode == "silent":
            return Admission("skip", "after_hours")
        if bot.after_hours_mode == "fixed_message":
            if not bot.after_hours_message.strip():
                return Admission("skip", "after_hours")
            return Admission("fixed_message", "after_hours")
    return Admission("reply")
