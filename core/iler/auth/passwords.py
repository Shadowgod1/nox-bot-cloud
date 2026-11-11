"""Password hashing (argon2id) and policy."""

import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 10
MAX_LENGTH = 256

_hasher = PasswordHasher()  # argon2id with library defaults
# Verified when the e-mail is unknown, so response time does not reveal which accounts exist.
_DUMMY_HASH = _hasher.hash("iler-dummy-password")


def validate_policy(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"password must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise ValueError(f"password must be at most {MAX_LENGTH} characters")
    return password


def hash_password_sync(password: str) -> str:
    return _hasher.hash(password)


def verify_password_sync(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(hash_password_sync, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-ish time check; pass None for an unknown user to burn the same CPU."""
    return await asyncio.to_thread(verify_password_sync, password_hash, password)
