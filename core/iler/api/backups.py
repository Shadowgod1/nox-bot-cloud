"""Backups API (owner only): list, create, download, restore."""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from iler.auth.deps import csrf_protect, require_owner
from iler.backup import restore as restore_mod
from iler.backup import run as run_mod
from iler.backup import status as backup_status
from iler.backup.run import BackupInfo
from iler.db.pool import get_pool
from iler.errors import ApiError

log = logging.getLogger("iler.api.backups")
router = APIRouter()


async def audit(user, action: str, details: dict) -> None:
    try:
        await get_pool().execute(
            "INSERT INTO audit_log(user_id, action, details) VALUES ($1, $2, $3)",
            getattr(user, "id", None),
            action,
            details,
        )
    except Exception:  # noqa: BLE001 - audit must never break the operation itself
        log.exception("audit_log insert failed for %s", action)


def _path(name: str):
    try:
        path = run_mod.resolve_backup(name)
    except run_mod.InvalidBackupName:
        raise ApiError(400, "invalid_backup_name", "Invalid backup name") from None
    return path


@router.get("/api/backups")
async def get_backups(user=Depends(require_owner)) -> dict:
    return {
        "items": [b.model_dump(mode="json") for b in run_mod.list_backups()],
        "last_status": await backup_status.get(),
    }


@router.post("/api/backups", dependencies=[Depends(csrf_protect)])
async def post_backups(user=Depends(require_owner)) -> BackupInfo:
    if run_mod.lock.locked():
        raise ApiError(409, "backup_in_progress", "Another backup or restore is running")
    try:
        info = await run_mod.create_backup()
    except run_mod.BackupError as e:
        log.error("manual backup failed: %s", e)
        await backup_status.safe_record(False, str(e))
        raise ApiError(500, "backup_failed", str(e)) from e
    await backup_status.safe_record(True, info.name)
    await audit(user, "backup.create", {"name": info.name, "size": info.size})
    return info


# `:path` so that traversal attempts reach the handler and get a clean 400 instead of a 404.
@router.get("/api/backups/{name:path}")
async def get_backups_name(name: str, user=Depends(require_owner)):
    path = _path(name)
    if not path.is_file():
        raise ApiError(404, "not_found", "Backup not found")
    return FileResponse(path, media_type="application/gzip", filename=path.name)


@router.post("/api/backups/{name:path}/restore", dependencies=[Depends(csrf_protect)])
async def post_backups_name_restore(name: str, user=Depends(require_owner)) -> dict:
    _path(name)
    if run_mod.lock.locked():
        raise ApiError(409, "backup_in_progress", "Another backup or restore is running")
    try:
        result = await restore_mod.restore_backup(name)
    except run_mod.InvalidBackupName:
        raise ApiError(400, "invalid_backup_name", "Invalid backup name") from None
    except FileNotFoundError:
        raise ApiError(404, "not_found", "Backup not found") from None
    except run_mod.BackupError as e:
        log.error("restore of %s failed: %s", name, e)
        raise ApiError(500, "restore_failed", str(e)) from e
    await audit(user, "backup.restore", result)
    return result
