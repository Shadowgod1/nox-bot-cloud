"""PII masking before the LLM call and restoring in the reply (spec 5.1 step 7).

Masked: phone numbers, e-mails, payment card numbers, Kazakhstan IIN (12 digits).
One `PiiMasker` lives for one turn: the same value always maps to the same placeholder, so the
model can refer to it consistently and `restore` puts the original back into its answer and tool args.
Dates, times and small numbers are left alone: a candidate needs at least 10 digits.
"""

import re
import unicodedata
from typing import Any

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Digit runs with phone/card separators; classified by digit count below.
_NUMBERISH = re.compile(r"(?<![\w+])\+?\d[\d \-()]{7,}\d(?!\w)")
_PLACEHOLDER = re.compile(r"\[(PHONE|EMAIL|CARD|IIN)_\d+\]")


def _classify(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    n = len(digits)
    if 13 <= n <= 19:
        return "CARD"
    if n == 12 and raw.isdigit():
        return "IIN"
    if 10 <= n <= 12:
        return "PHONE"
    return None


class PiiMasker:
    def __init__(self) -> None:
        self._by_value: dict[str, str] = {}
        self._by_token: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def _token(self, kind: str, value: str) -> str:
        token = self._by_value.get(value)
        if token is None:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            token = f"[{kind}_{self._counters[kind]}]"
            self._by_value[value] = token
            self._by_token[token] = value
        return token

    def mask(self, text: str) -> str:
        if not text:
            return text
        text = _EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), text)

        def _num(m: re.Match) -> str:
            raw = m.group(0)
            kind = _classify(raw)
            return self._token(kind, raw) if kind else raw

        return _NUMBERISH.sub(_num, text)

    def restore(self, text: str) -> str:
        if not text or not self._by_token:
            return text
        return _PLACEHOLDER.sub(lambda m: self._by_token.get(m.group(0), m.group(0)), text)

    def restore_obj(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, dict):
            return {k: self.restore_obj(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.restore_obj(v) for v in value]
        return value


def one_line(value: Any, limit: int) -> str:
    """Client/model text forwarded to admins: control and format characters (newlines, NUL, bidi overrides,
    zero-width) become spaces, whitespace is collapsed, «» are replaced so the text cannot close a quote,
    and the result is cut to `limit` characters."""
    if value is None:
        return ""
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in str(value))
    text = re.sub(r"\s+", " ", text.replace("«", '"').replace("»", '"')).strip()
    if len(text) > limit:
        text = text[: max(limit - 1, 0)].rstrip() + "…"
    return text


def mask_phone_for_log(phone: str) -> str:
    """Logs carry no PII (spec 9): keep only the last 4 digits."""
    digits = re.sub(r"\D", "", phone or "")
    return f"***{digits[-4:]}" if digits else ""
