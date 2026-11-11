"""Restore a backup archive.

Both Postgres databases are restored in place with ``pg_restore --clean --if-exists``. A safety
backup of the current state is taken first. Redis cannot be swapped from inside iler-core (the RDB
lives in the redis volume and needs a redis restart), so ``redis.rdb`` is staged into
``${data_dir}/restore/redis.rdb`` for the updater / ``install.sh --restore`` to apply.
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
                await run_mod.run_cmd(
                    [
                        "pg_restore", "--clean", "--if-exists", "--no-owner", "--single-transaction",
                        f"--dbname={target}", str(work / fname),
                    ],
                    env=env,
                )
            staged_dir = Path(settings.data_dir) / "restore"
            staged_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(work / "redis.rdb", staged_dir / "redis.rdb")
            log.warning("backup %s restored; redis snapshot staged at %s", name, staged_dir / "redis.rdb")
            return {
                "restored": name,
                "version": manifest.get("version"),
                "safety_backup": safety.name,
                "redis": "staged",
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)
