"""System API (owner only): version and update via iler-updater."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from iler import settings_store
from iler.api.backups import audit
from iler.auth.deps import csrf_protect, require_owner
from iler.config import VERSION
from iler.errors import ApiError
from iler.updater_client import UpdaterClient, get_updater_client

router = APIRouter()

UPDATE_JOB_KEY = "system_update"


class UpdateJob(BaseModel):
    """Last update job, persisted: iler-core itself is restarted during an update."""

    job_id: str | None = None
    started_at: str | None = None  # ISO string: settings_store dumps in python mode (no datetime)


@router.get("/api/system/version")
async def get_system_version(
    user=Depends(require_owner), updater: UpdaterClient = Depends(get_updater_client)
) -> dict:
    try:
        v = await updater.get_version()
    except ApiError:
        return {"current": None, "available": None, "core_version": VERSION, "updater_available": False}
    return {
        "current": v.get("current"),
        "available": v.get("available"),
        "core_version": VERSION,
        "updater_available": True,
    }


@router.post("/api/system/update", dependencies=[Depends(csrf_protect)])
async def post_system_update(
    user=Depends(require_owner), updater: UpdaterClient = Depends(get_updater_client)
) -> dict:
    # The updater takes the pre-update backup itself (job state "backup").
    resp = await updater.start_update()
    job_id = resp.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ApiError(502, "updater_error", "Updater did not return a job id")
    await settings_store.put(UPDATE_JOB_KEY, UpdateJob(job_id=job_id, started_at=datetime.now(UTC).isoformat()))
    await audit(user, "system.update", {"job_id": job_id})
    return {"job_id": job_id}


@router.get("/api/system/update/status")
async def get_system_update_status(
    job_id: str | None = None,
    user=Depends(require_owner),
    updater: UpdaterClient = Depends(get_updater_client),
) -> dict:
    if not job_id:
        job_id = (await settings_store.get(UPDATE_JOB_KEY, UpdateJob)).job_id
    if not job_id:
        return {"job_id": None, "state": None, "log": ""}
    st = await updater.update_status(job_id)
    return {"job_id": job_id, "state": st.get("state"), "log": st.get("log") or ""}
