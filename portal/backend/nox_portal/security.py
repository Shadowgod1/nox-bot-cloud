"""Passwords (argon2id), session JWT, CSRF tokens, e-mail code hashing and secret sealing.

Patterns follow iler-core (core/iler/auth): argon2id with a dummy hash for unknown accounts, HS256 session
JWT carrying `pwv` (fingerprint of the password hash, so a password change ends older sessions), CSRF
double-submit cookie. All keys are HKDF subkeys of NOX_SECRET_KEY, one per purpose.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from functools import lru_cache
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from nox_portal.config import get_settings

# ---- passwords ------------------------------------------------------------------------------

MIN_PASSWORD = 10
MAX_PASSWORD = 256
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("nox-dummy-password")


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"пароль должен быть не короче {MIN_PASSWORD} символов")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"пароль должен быть не длиннее {MAX_PASSWORD} символов")
    return password


def _verify_sync(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    """Pass None for an unknown account: the same CPU is burnt, so timing does not reveal it."""
    return await asyncio.to_thread(_verify_sync, password_hash, password)


def hash_password_sync(password: str) -> str:
    return _hasher.hash(password)


# ---- keys -----------------------------------------------------------------------------------


@lru_cache(maxsize=16)
def _derive(secret: str, info: bytes) -> bytes:
    try:
        ikm = base64.b64decode(secret, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("NOX_SECRET_KEY must be base64") from exc
    if len(ikm) < 32:
        raise RuntimeError("NOX_SECRET_KEY must decode to at least 32 bytes")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(ikm)


def subkey(purpose: str) -> bytes:
    return _derive(get_settings().secret_key, b"nox-portal-" + purpose.encode())


# ---- sessions -------------------------------------------------------------------------------

SESSION_COOKIE = "nox_session"
CSRF_COOKIE = "nox_csrf"
CSRF_HEADER = "X-Nox-CSRF"
SESSION_TTL = 12 * 3600
REFRESH_BELOW = 6 * 3600
ALGORITHM = "HS256"


class InvalidSession(Exception):
    pass


def password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def create_session(kind: str, subject_id: int, password_hash: str, now: float | None = None) -> str:
    """kind: "client" or "staff"; the two never share an id space."""
    iat = int(now if now is not None else time.time())
    claims = {
        "sub": str(subject_id),
        "knd": kind,
        "iat": iat,
        "exp": iat + SESSION_TTL,
        "pwv": password_fingerprint(password_hash),
    }
    return jwt.encode(claims, subkey("session"), algorithm=ALGORITHM)


def decode_session(token: str) -> dict:
    try:
        claims = jwt.decode(
            token, subkey("session"), algorithms=[ALGORITHM],
            options={"require": ["sub", "knd", "exp", "iat", "pwv"]},
        )
        claims["uid"] = int(claims["sub"])
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise InvalidSession(str(exc)) from exc
    if claims["knd"] not in ("client", "staff"):
        raise InvalidSession("unknown session kind")
    return claims


def needs_refresh(claims: dict, now: float | None = None) -> bool:
    return claims["exp"] - (now if now is not None else time.time()) < REFRESH_BELOW


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


# ---- e-mail codes ---------------------------------------------------------------------------


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(account_id: int, purpose: str, code: str) -> str:
    msg = f"{account_id}:{purpose}:{code}".encode()
    return hmac.new(subkey("email-code"), msg, hashlib.sha256).hexdigest()


# ---- symmetric encryption (setup links) -----------------------------------------------------

_PREFIX = "enc:v1:"


def encrypt(plain: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(subkey("setup-link")).encrypt(nonce, plain.encode("utf-8"), None)
    return _PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def decrypt(token: str) -> str:
    if not token.startswith(_PREFIX):
        raise ValueError("not an encrypted token")
    blob = base64.b64decode(token[len(_PREFIX):])
    return AESGCM(subkey("setup-link")).decrypt(blob[:12], blob[12:], None).decode("utf-8")


# ---- sealing for the provisioner (RSA-OAEP-SHA256) -------------------------------------------


class SealingUnavailable(Exception):
    pass


def load_provisioner_pubkey() -> rsa.RSAPublicKey:
    path = Path(get_settings().provisioner_pubkey_file)
    try:
        key = serialization.load_pem_public_key(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise SealingUnavailable(f"provisioner public key not readable: {path}") from exc
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise SealingUnavailable("provisioner public key must be RSA >= 2048 bits")
    return key


def seal_for_provisioner(plain: str) -> str:
    """Only the provisioner's private key (root-only file on the host) can open this:
    `openssl pkeyutl -decrypt -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256`."""
    key = load_provisioner_pubkey()
    ct = key.encrypt(
        plain.encode("utf-8"),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return base64.b64encode(ct).decode("ascii")


def token_matches_sha256(token: str, expected_hex: str) -> bool:
    if not token or not expected_hex:
        return False
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, expected_hex.strip().lower())


# ---- log / error hygiene --------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9._-]+"), "sk-ant-***"),
    (re.compile(r"(token=)[^\s&\"']+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"((?:key|secret|password|token)\s*[=:]\s*)[^\s,;\"']+", re.IGNORECASE), r"\1***"),
]


def sanitize(text: str | None, limit: int = 2000) -> str | None:
    """Strip anything that looks like a key, token or password from text we store or show."""
    if text is None:
        return None
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text[:limit]
