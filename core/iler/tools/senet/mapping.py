"""Defensive mapping of SENET `/workstations/` items.

The exact response schema is unknown until we get access to a club's `/docs`; everything here
tolerates missing / differently named fields. Unknown state -> "unknown" (never counted as free).
"""

from dataclasses import dataclass
from typing import Any, Literal

State = Literal["free", "busy", "reserved", "offline", "unknown"]

LIST_KEYS = ("results", "items", "data", "workstations", "objects")
NAME_KEYS = ("name", "title", "number", "hostname", "host", "label")
ZONE_KEYS = ("zone", "group", "zone_name", "group_name", "room", "hall", "area")
NESTED_NAME_KEYS = ("name", "title", "label")
NEGATIONS = ("un", "not", "no ", "no_", "no-", "non")

_STATE_WORDS: dict[State, tuple[str, ...]] = {
    "offline": ("offline", "off", "disabled", "maintenance", "shutdown", "broken", "repair", "выключ", "ремонт"),
    "reserved": ("reserved", "booked", "booking", "reservation", "заброн", "резерв"),
    "busy": ("busy", "occupied", "in use", "in_use", "inuse", "session", "playing", "locked_by_user", "занят"),
    "free": ("free", "available", "idle", "vacant", "ready", "свобод"),
}


@dataclass(frozen=True)
class Workstation:
    name: str
    zone: str | None
    state: State


def extract_items(data: Any) -> list[dict]:
    """Accept a list or a wrapper object like {results|items|data: [...]}; drop non-dict items."""
    if isinstance(data, dict):
        for key in LIST_KEYS:
            if key in data:
                return extract_items(data[key])
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        s = str(value).strip()
        return s or None
    if isinstance(value, dict):
        for key in NESTED_NAME_KEYS:
            s = _text(value.get(key))
            if s:
                return s
    return None


def _first(raw: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        s = _text(raw.get(key))
        if s:
            return s
    return None


def _word_state(value: Any) -> State | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("code") or value.get("value")
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if not v:
        return None
    if v.startswith(NEGATIONS):  # "unavailable", "not_busy", "no_session": ambiguous -> unknown
        return None
    for state in ("offline", "reserved", "busy", "free"):
        if any(word in v for word in _STATE_WORDS[state]):
            return state  # type: ignore[return-value]
    return None


def _state(raw: dict) -> State:
    for key in ("online", "is_online"):
        if raw.get(key) is False:
            return "offline"
    for key in ("state", "status"):
        s = _word_state(raw.get(key))
        if s:
            return s
    for key in ("reserved", "is_reserved", "booking", "reservation"):
        if key in raw and raw[key]:
            return "reserved"
    for key in ("is_busy", "busy"):
        if isinstance(raw.get(key), bool):
            return "busy" if raw[key] else "free"
    if "session" in raw:
        return "busy" if raw["session"] else "free"
    return "unknown"


def to_workstation(raw: dict) -> Workstation:
    if not isinstance(raw, dict):
        raw = {}
    name = _first(raw, NAME_KEYS) or (f"#{raw['id']}" if _text(raw.get("id")) else "?")
    zone = _first(raw, ZONE_KEYS)
    return Workstation(name=name, zone=zone, state=_state(raw))
