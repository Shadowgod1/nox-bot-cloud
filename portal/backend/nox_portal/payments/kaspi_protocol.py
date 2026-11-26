"""Wire format of the Kaspi "Платежи на Kaspi.kz" service-provider protocol (check / pay callbacks).

TODO(kaspi): NOT VERIFIED. This follows the commonly used QIWI-style provider protocol (command, txn_id, account,
sum, txn_date -> txn_id, result, comment, prv_txn) as described by integrators. Check every parameter name, the
response format (JSON vs XML), the result codes and the date format against the official Kaspi protocol document
once it is received with the contract, and adjust ONLY this module.

Request (GET query string or POST application/x-www-form-urlencoded):
  command   check | pay
  txn_id    Kaspi transaction id (idempotency key of pay)
  account   our payment code: the invoice number digits (NOX-2026-00012 -> 202600012)
  sum       amount in tenge, e.g. "70000.00" (check may omit it)
  txn_date  pay only, YYYYMMDDHHMMSS

Response (JSON, HTTP 200): {"txn_id", "result", "comment", "sum"?, "prv_txn"? (pay)}
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs

# result codes
OK = 0
TEMPORARY_ERROR = 1
BAD_FORMAT = 4
ACCOUNT_NOT_FOUND = 5  # also: already paid / not payable any more
SUM_TOO_SMALL = 241
SUM_TOO_LARGE = 242
OTHER_ERROR = 300

_TXN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class BadRequest(Exception):
    pass


@dataclass(frozen=True)
class KaspiRequest:
    command: str
    txn_id: str
    account: str
    sum: Decimal | None
    txn_date: str


def account_code(invoice_number: str) -> str:
    """The payment code Kaspi shows and sends back: digits of the invoice number (unique, stable per invoice)."""
    return "".join(c for c in invoice_number if c.isdigit())


def parse(query: dict[str, str], body: bytes, content_type: str) -> KaspiRequest:
    params = dict(query)
    if body and "application/x-www-form-urlencoded" in content_type:
        for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items():
            params.setdefault(k, v[0] if v else "")
    command = params.get("command", "").strip().lower()
    txn_id = params.get("txn_id", "").strip()
    account = account_code(params.get("account", ""))
    if not txn_id or not _TXN_RE.match(txn_id):
        raise BadRequest("txn_id")
    if not account or len(account) > 32:
        raise BadRequest("account")
    raw_sum = params.get("sum", "").strip().replace(",", ".")
    amount = None
    if raw_sum:
        try:
            amount = Decimal(raw_sum)
        except InvalidOperation:
            raise BadRequest("sum") from None
        if amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise BadRequest("sum")
    return KaspiRequest(command=command, txn_id=txn_id, account=account, sum=amount,
                        txn_date=params.get("txn_date", "").strip()[:14])


def response(txn_id: str, result: int, comment: str = "", *, amount: int | None = None,
             prv_txn: str | None = None) -> dict:
    out: dict = {"txn_id": txn_id, "result": result, "comment": comment}
    if amount is not None:
        out["sum"] = f"{amount}.00"
    if prv_txn is not None:
        out["prv_txn"] = prv_txn
    return out
