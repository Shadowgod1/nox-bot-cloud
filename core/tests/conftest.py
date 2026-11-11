"""Shared fixtures.

Requires a Postgres reachable at ILER_TEST_DATABASE_URL (admin DSN; a fresh database is created per run):
    docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=test postgres:16-alpine
"""

import base64
import os
import uuid

import asyncpg
import pytest

ADMIN_DSN = os.environ.get("ILER_TEST_DATABASE_URL", "postgresql://postgres:test@localhost:55432/postgres")
TEST_DB = f"iler_test_{uuid.uuid4().hex[:8]}"
_base, _ = ADMIN_DSN.rsplit("/", 1)

os.environ["ILER_DATABASE_URL"] = f"{_base}/{TEST_DB}"
os.environ.setdefault("ILER_SECRET_KEY", base64.b64encode(b"k" * 32).decode())
os.environ.setdefault("ILER_EVO_API_KEY", "test-evo-key")
os.environ.setdefault("ILER_UPDATER_TOKEN", "test-updater-token")
os.environ.setdefault("ILER_SETUP_TOKEN", "test-setup-token")
os.environ.setdefault("ILER_OPENAPI", "1")
os.environ["ILER_DISABLE_WORKERS"] = "1"

from asgi_lifespan import LifespanManager  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from iler.db.pool import get_pool  # noqa: E402
from iler.main import create_app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def _database():
    conn = await asyncpg.connect(ADMIN_DSN)
    await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    await conn.close()
    yield
    from iler.db.pool import close_pool

    await close_pool()
    conn = await asyncpg.connect(ADMIN_DSN)
    await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    await conn.close()


@pytest.fixture(scope="session")
async def app(_database):
    application = create_app()
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def pool(app):
    return get_pool()


@pytest.fixture(autouse=True)
async def clean_db(request):
    """Truncate all tables (except schema_migrations) before each test that touches the app."""
    if "app" not in request.fixturenames and "client" not in request.fixturenames and "pool" not in request.fixturenames:
        yield
        return
    app = request.getfixturevalue("app")  # noqa: F841 - ensure lifespan started
    p = get_pool()
    tables = [
        r["tablename"]
        for r in await p.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations'"
        )
    ]
    if tables:
        await p.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
    yield
