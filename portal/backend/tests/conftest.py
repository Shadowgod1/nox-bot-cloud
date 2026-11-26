"""Shared fixtures.

Needs a Postgres admin DSN in NOX_TEST_DATABASE_URL (the database nox_portal_test is created per run):
    docker start iler-test-pg
    NOX_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres python -m pytest -q
"""

import base64
import hashlib
import os
import tempfile

import asyncpg
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ADMIN_DSN = os.environ.get("NOX_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/postgres")
TEST_DB = "nox_portal_test"
_base, _ = ADMIN_DSN.rsplit("/", 1)
PROVISIONER_TOKEN = "test-provisioner-token-0123456789"

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_pub = tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False)
_pub.write(PRIVATE_KEY.public_key().public_bytes(serialization.Encoding.PEM,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo))
_pub.close()

os.environ["NOX_DATABASE_URL"] = f"{_base}/{TEST_DB}"
os.environ["NOX_SECRET_KEY"] = base64.b64encode(b"s" * 32).decode()
os.environ["NOX_EMAIL_MODE"] = "memory"
os.environ["NOX_PUBLIC_URL"] = "https://bot.example.kz"
os.environ["NOX_INSTANCE_DOMAIN"] = "example.kz"
os.environ["NOX_STAFF_NOTIFY"] = "staff@example.kz"
os.environ["NOX_PROVISIONER_TOKEN_SHA256"] = hashlib.sha256(PROVISIONER_TOKEN.encode()).hexdigest()
os.environ["NOX_PROVISIONER_PUBKEY_FILE"] = _pub.name

from asgi_lifespan import LifespanManager  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from nox_portal import mailer  # noqa: E402
from nox_portal.db import close_pool, get_pool  # noqa: E402
from nox_portal.main import create_app, create_internal_app  # noqa: E402
from nox_portal.security import hash_password_sync  # noqa: E402


def unseal(blob: str) -> str:
    return PRIVATE_KEY.decrypt(
        base64.b64decode(blob),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    ).decode()


@pytest.fixture(scope="session", autouse=True)
async def _database():
    conn = await asyncpg.connect(ADMIN_DSN)
    await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    await conn.close()
    yield
    await close_pool()
    conn = await asyncpg.connect(ADMIN_DSN)
    await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    await conn.close()
    os.unlink(_pub.name)


@pytest.fixture(scope="session")
async def app(_database):
    application = create_app()
    async with LifespanManager(application):
        yield application


@pytest.fixture(scope="session")
async def internal_app(app):
    return create_internal_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://bot.example.kz") as c:
        yield c


@pytest.fixture
async def other(app):
    """A second browser (separate cookie jar)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://bot.example.kz") as c:
        yield c


@pytest.fixture
async def internal(internal_app):
    async with AsyncClient(transport=ASGITransport(app=internal_app), base_url="http://127.0.0.1:8099",
                           headers={"X-Nox-Provisioner-Token": PROVISIONER_TOKEN}) as c:
        yield c


@pytest.fixture
async def pool(app):
    return get_pool()


@pytest.fixture(autouse=True)
async def clean_db(request):
    mailer.OUTBOX.clear()
    if not {"app", "client", "pool", "internal", "other"} & set(request.fixturenames):
        yield
        return
    request.getfixturevalue("app")
    p = get_pool()
    tables = [r["tablename"] for r in await p.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations'")]
    # the payments ledger refuses TRUNCATE (append-only trigger): lift it for the test reset only
    async with p.acquire() as conn, conn.transaction():
        await conn.execute("ALTER TABLE payments DISABLE TRIGGER payments_no_truncate")
        await conn.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
        await conn.execute("ALTER TABLE payments ENABLE TRIGGER payments_no_truncate")
        await conn.execute("ALTER SEQUENCE invoice_seq RESTART")
    yield


# ---- helpers --------------------------------------------------------------------------------

REG = {
    "company": "ТОО Пример",
    "contact_name": "Айгерим",
    "email": "owner@example.kz",
    "phone": "+7 701 000 00 00",
    "slug": "primer",
    "password": "correct-horse-1",
    "consent": True,
}


def last_code(to: str) -> str:
    import re

    for mail in reversed(mailer.OUTBOX):
        if mail.to == to:
            m = re.search(r"\b(\d{6})\b", mail.text)
            if m:
                return m.group(1)
    raise AssertionError(f"no code mailed to {to}")


def csrf(c: AsyncClient) -> dict:
    return {"X-Nox-CSRF": c.cookies.get("nox_csrf") or ""}


async def register(c: AsyncClient, **over) -> dict:
    body = {**REG, **over}
    r = await c.post("/api/auth/register", json=body)
    assert r.status_code == 202, r.text
    return body


async def register_verified(c: AsyncClient, **over) -> dict:
    body = await register(c, **over)
    r = await c.post("/api/auth/verify", json={"email": body["email"], "code": last_code(body["email"])})
    assert r.status_code == 200, r.text
    return body


async def make_staff(pool, email: str = "ops@example.kz", password: str = "staff-password-1") -> int:
    return await pool.fetchval("INSERT INTO staff(email, name, password_hash) VALUES ($1, 'Ops', $2) RETURNING id",
                               email, hash_password_sync(password))


async def staff_login(c: AsyncClient, pool, email: str = "ops@example.kz") -> None:
    await make_staff(pool, email)
    r = await c.post("/api/staff/auth/login", json={"email": email, "password": "staff-password-1"})
    assert r.status_code == 200, r.text
