"""Session JWT (HS256) in the `iler_session` cookie and the CSRF double-submit token.

The signing key is derived from ILER_SECRET_KEY with HKDF (info "iler-session"), so the
settings-encryption key is never used directly for signing. Each token carries `pwv`, a short
fingerprint of the user's password hash: changing the password invalidates older sessions.
"""

import base64
import hashlib
import secrets
import time
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from iler.config import get_settings

SESSION_COOKIE = "iler_session"
CSRF_COOKIE = "iler_csrf"
CSRF_HEADER = "X-ILER-CSRF"
SESSION_TTL = 12 * 3600
REFRESH_BELOW = 6 * 3600  # re-issue when less than this remains
ALGORITHM = "HS256"


class InvalidSession(Exception):
    pass


@lru_cache(maxsize=4)
def _derive(secret: str) -> bytes:
    try:
        ikm = base64.b64decode(secret, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("ILER_SECRET_KEY must be base64") from exc
    if len(ikm) < 32:
        raise RuntimeError("ILER_SECRET_KEY must decode to at least 32 bytes")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"iler-session").derive(ikm)


def session_key() -> bytes:
    return _derive(get_settings().secret_key)


def password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def create_session(user_id: int, password_hash: str, now: float | None = None) -> str:
    iat = int(now if now is not None else time.time())
    claims = {"sub": str(user_id), "iat": iat, "exp": iat + SESSION_TTL, "pwv": password_fingerprint(password_hash)}
    return jwt.encode(claims, session_key(), algorithm=ALGORITHM)


def decode_session(token: str) -> dict:
    try:
        claims = jwt.decode(
            token, session_key(), algorithms=[ALGORITHM], options={"require": ["sub", "exp", "iat", "pwv"]}
        )
        claims["uid"] = int(claims["sub"])
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise InvalidSession(str(exc)) from exc
    return claims


def needs_refresh(claims: dict, now: float | None = None) -> bool:
    return claims["exp"] - (now if now is not None else time.time()) < REFRESH_BELOW


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
