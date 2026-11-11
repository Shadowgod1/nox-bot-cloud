"""Restore a backup archive.

Both Postgres databases are restored in place with ``pg_restore --clean --if-exists``. A safety
backup of the current state is taken first. The evolution database is restored as role
``evolution`` (``--role``) and privileges are re-granted afterwards, so Evolution API keeps access to
its tables.

Redis is not touched here: the RDB lives in the redis volume and can only be swapped while redis is
stopped, which iler-core cannot do. ``install.sh --restore <file>`` applies ``redis.rdb`` from the
same archive (WhatsApp session); a restore from the admin UI reports ``"redis": "skipped"``.
"""

import asyncio
import json
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path

from iler.backup import run as run_mod
from iler.config import get_settings

log = logging.getLogger("iler.backup")

MEMBERS = (*run_mod.DUMP_FILES, "manifest.json")
EVOLUTION_ROLE = "evolution"
# Ownership fix-up for objects restored into the evolution database (idempotent).
EVOLUTION_GRANTS = (
    "ALTER SCHEMA public OWNER TO evolution;"
    " GRANT ALL ON ALL TABLES IN SCHEMA public TO evolution;"
    " GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO evolution;"
    " GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO evolution;"
    " ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO evolution;"
    " ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO evolution;"
)
REDIS_NOTE = (
    "The WhatsApp session snapshot (redis) is applied only by 'install.sh --restore <file>' on the server;"
    " otherwise reconnect WhatsApp after the restore if needed."
)


def _extract_verified(archive: Path, dest: Path) -> dict:
    """Extract only the expected regular files, then check them against the manifest."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = {m.name: m for m in tar.getmembers()}
            for name in MEMBERS:
                m = members.get(name)
                if m is None or not m.isfile():
                    raise run_mod.BackupError(f"archive is missing {name}")
                src = tar.extractfile(m)
                with open(dest / name, "wb") as out:
                    shutil.copyfileobj(src, out)
    except (tarfile.TarError, OSError, EOFError) as e:
        raise run_mod.BackupError(f"cannot read archive: {e}") from e
    try:
        manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        files = manifest["files"]
    except (ValueError, KeyError, TypeError) as e:
        raise run_mod.BackupError("manifest.json is invalid") from e
    for name in run_mod.DUMP_FILES:
        meta = files.get(name) if isinstance(files, dict) else None
        if not meta or meta.get("sha256") != run_mod._sha256(dest / name):
            raise run_mod.BackupError(f"checksum mismatch for {name}")
    return manifest


async def restore_backup(name: str) -> dict:
    """Restore backup `name`. Raises InvalidBackupName, FileNotFoundError or BackupError."""
    archive = run_mod.resolve_backup(name)
    if not archive.is_file():
        raise FileNotFoundError(name)
    settings = get_settings()
    async with run_mod.lock:
        work = Path(tempfile.mkdtemp(prefix=".work-", dir=archive.parent))
        try:
            manifest = await asyncio.to_thread(_extract_verified, archive, work)
            safety = await run_mod._create_locked()
            for fname, db in (("iler.dump", None), ("evolution.dump", run_mod.EVOLUTION_DB)):
                target, env = run_mod.pg_target(settings.database_url, db)
                role = [f"--role={EVOLUTION_ROLE}"] if db == run_mod.EVOLUTION_DB else []
                await run_mod.run_cmd(
                    [
                        "pg_restore", "--clean", "--if-exists", "--no-owner", *role, "--single-transaction",
                        f"--dbname={target}", str(work / fname),
                    ],
                    env=env,
                )
            target, env = run_mod.pg_target(settings.database_url, run_mod.EVOLUTION_DB)
            await run_mod.run_cmd(
                ["psql", "-v", "ON_ERROR_STOP=1", "-q", f"--dbname={target}", "-c", EVOLUTION_GRANTS], env=env
            )
            log.warning("backup %s restored (databases); redis snapshot was not applied", name)
            return {
                "restored": name,
                "version": manifest.get("version"),
                "safety_backup": safety.name,
                "redis": "skipped",
                "redis_note": REDIS_NOTE,
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)
