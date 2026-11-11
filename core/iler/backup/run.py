"""Create, list and prune backups.

A backup is ``iler-backup-YYYYMMDD-HHMMSS.tar.gz`` in ``${data_dir}/backups`` containing:
``iler.dump`` and ``evolution.dump`` (``pg_dump -Fc``), ``redis.rdb`` (``redis-cli --rdb``) and
``manifest.json`` with the SHA-256 of every file.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from iler.config import VERSION, get_settings

log = logging.getLogger("iler.backup")

NAME_RE = re.compile(r"^iler-backup-\d{8}-\d{6}(?:-\d{1,3})?\.tar\.gz$")
RETENTION_DAYS = 14
DUMP_FILES = ("iler.dump", "evolution.dump", "redis.rdb")
EVOLUTION_DB = "evolution"
REDIS_HOST = "redis"
REDIS_PORT = 6379
CMD_TIMEOUT = 30 * 60

# One backup/restore at a time (scheduler, API and pre-restore backup share it).
lock = asyncio.Lock()


class BackupError(Exception):
    pass


class InvalidBackupName(ValueError):
    pass


class BackupInfo(BaseModel):
    name: str
    size: int
    created_at: datetime
    sha256: str | None = None


async def run_cmd(args: list[str], env: dict | None = None) -> bytes:
    """Run an external tool; raise BackupError on non-zero exit. Replaced in tests."""
    full_env = {**os.environ, **(env or {})}
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, env=full_env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as e:
        raise BackupError(f"{args[0]} not found") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), CMD_TIMEOUT)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip()[-500:]
        raise BackupError(f"{os.path.basename(args[0])} exited {proc.returncode}: {tail}")
    return out


def backups_dir() -> Path:
    return Path(get_settings().data_dir) / "backups"


def resolve_backup(name: str) -> Path:
    """Map a user-supplied backup name to a path inside backups_dir(); reject anything else."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise InvalidBackupName(name)
    base = backups_dir().resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise InvalidBackupName(name)
    return path


def pg_target(dsn: str, dbname: str | None = None) -> tuple[str, dict]:
    """Return (dsn without password, env with PGPASSWORD) so the password never hits argv."""
    parts = urlsplit(dsn)
    env: dict = {}
    if parts.password is not None:
        env["PGPASSWORD"] = unquote(parts.password)
    netloc = parts.hostname or ""
    if parts.username:
        netloc = f"{parts.username}@{netloc}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    path = f"/{dbname}" if dbname else parts.path
    return urlunsplit((parts.scheme, netloc, path, parts.query, "")), env


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _new_name(directory: Path) -> str:
    stamp = datetime.now(ZoneInfo(get_settings().tz)).strftime("%Y%m%d-%H%M%S")
    name = f"iler-backup-{stamp}.tar.gz"
    n = 1
    while (directory / name).exists():
        name = f"iler-backup-{stamp}-{n}.tar.gz"
        n += 1
    return name


async def _dump_all(work: Path) -> None:
    dsn = get_settings().database_url
    for fname, db in (("iler.dump", None), ("evolution.dump", EVOLUTION_DB)):
        target, env = pg_target(dsn, db)
        await run_cmd(["pg_dump", "-Fc", "--no-owner", "-f", str(work / fname), f"--dbname={target}"], env=env)
    await run_cmd(["redis-cli", "-h", REDIS_HOST, "-p", str(REDIS_PORT), "--rdb", str(work / "redis.rdb")])
    for fname in DUMP_FILES:
        if not (work / fname).is_file():
            raise BackupError(f"{fname} was not produced")


async def _create_locked() -> BackupInfo:
    directory = backups_dir()
    directory.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=directory))
    try:
        await _dump_all(work)
        created = datetime.now(ZoneInfo(get_settings().tz))
        manifest = {
            "format": 1,
            "version": VERSION,
            "created_at": created.isoformat(),
            "files": {f: {"sha256": _sha256(work / f), "size": (work / f).stat().st_size} for f in DUMP_FILES},
        }
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        name = _new_name(directory)
        partial = work / (name + ".partial")

        def _pack() -> None:
            with tarfile.open(partial, "w:gz") as tar:
                for f in (*DUMP_FILES, "manifest.json"):
                    tar.add(work / f, arcname=f)

        await asyncio.to_thread(_pack)
        final = directory / name
        os.replace(partial, final)
        sha = await asyncio.to_thread(_sha256, final)
        log.info("backup created: %s", name)
        return BackupInfo(name=name, size=final.stat().st_size, created_at=created, sha256=sha)
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def create_backup() -> BackupInfo:
    async with lock:
        return await _create_locked()


def list_backups() -> list[BackupInfo]:
    directory = backups_dir()
    if not directory.is_dir():
        return []
    tz = ZoneInfo(get_settings().tz)
    items = []
    for p in directory.iterdir():
        if p.is_file() and NAME_RE.fullmatch(p.name):
            st = p.stat()
            items.append(BackupInfo(name=p.name, size=st.st_size, created_at=datetime.fromtimestamp(st.st_mtime, tz)))
    items.sort(key=lambda b: (b.created_at, b.name), reverse=True)
    return items


def apply_retention(keep_days: int = RETENTION_DAYS, now: float | None = None) -> list[str]:
    """Delete our backup archives older than keep_days (by mtime). Returns removed names."""
    directory = backups_dir()
    if not directory.is_dir():
        return []
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    removed = []
    for p in directory.iterdir():
        if p.is_file() and NAME_RE.fullmatch(p.name) and p.stat().st_mtime < cutoff:
            try:
                p.unlink()
                removed.append(p.name)
            except OSError:
                log.exception("could not remove old backup %s", p.name)
    # leftovers of interrupted runs
    for p in directory.glob(".work-*"):
        if p.is_dir() and p.stat().st_mtime < cutoff:
            shutil.rmtree(p, ignore_errors=True)
    if removed:
        log.info("retention removed %d backup(s)", len(removed))
    return removed
