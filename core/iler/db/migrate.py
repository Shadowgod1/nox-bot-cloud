"""Applies numbered SQL files from core/migrations once, under an advisory lock."""

import os
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(os.environ.get("ILER_MIGRATIONS_DIR") or Path(__file__).resolve().parents[2] / "migrations")


async def run(pool: asyncpg.Pool, directory: Path = MIGRATIONS_DIR) -> list[str]:
    files = sorted(Path(directory).glob("*.sql"))
    if not files:
        raise RuntimeError(f"no migrations found in {directory}")
    applied: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock(hashtext('iler_migrate'))")
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
            for path in files:
                if path.name in done:
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute("INSERT INTO schema_migrations(name) VALUES ($1)", path.name)
                applied.append(path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext('iler_migrate'))")
    return applied
