"""AES-GCM encryption for secrets stored in the database."""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from iler.config import get_settings

PREFIX = "enc:v1:"


def _key() -> bytes:
    raw = get_settings().secret_key
    try:
        key = base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("ILER_SECRET_KEY must be base64") from exc
    if len(key) != 32:
        raise RuntimeError("ILER_SECRET_KEY must decode to 32 bytes")
    return key


def encrypt(plain: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, plain.encode("utf-8"), None)
    return PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def decrypt(token: str) -> str:
    if not token.startswith(PREFIX):
        raise ValueError("not an encrypted token")
    blob = base64.b64decode(token[len(PREFIX):])
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], None).decode("utf-8")
