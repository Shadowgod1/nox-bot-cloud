"""Internal API for the host-side provisioner.

Served by a separate ASGI app on its own port, published on 127.0.0.1 only (never through nginx or the
tunnel): the public app does not even have these routes. Every request needs the shared token in
X-Nox-Provisioner-Token; the portal stores only its SHA-256 (NOX_PROVISIONER_TOKEN_SHA256).
"""

from fastapi import APIRouter, Depends, Request, Response

from nox_portal import jobs
from nox_portal.config import get_settings
from nox_portal.db import get_pool
from nox_portal.errors import ApiError
from nox_portal.schemas import ClaimedJob, JobResult, Ok
from nox_portal.security import token_matches_sha256

TOKEN_HEADER = "X-Nox-Provisioner-Token"


async def require_provisioner(request: Request) -> None:
    if not token_matches_sha256(request.headers.get(TOKEN_HEADER, ""), get_settings().provisioner_token_sha256):
        raise ApiError(401, "unauthorized", "provisioner token missing or wrong")


router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_provisioner)])


@router.get("/ping", response_model=Ok)
async def get_ping():
    return Ok()


@router.post("/jobs/claim", response_model=ClaimedJob, responses={204: {"description": "no work"}})
async def post_claim():
    async with get_pool().acquire() as conn:
        job = await jobs.claim(conn)
    if job is None:
        return Response(status_code=204)
    return ClaimedJob(id=job["id"], kind=job["kind"], slug=job["slug"], coins=job["coins"],
                      public_url=jobs.public_url_for(job["slug"]), llm_key_sealed=job["llm_key_sealed"],
                      gateway_key_sealed=job["gateway_key_sealed"])


@router.post("/jobs/{job_id}/result", response_model=Ok)
async def post_result(job_id: int, body: JobResult):
    async with get_pool().acquire() as conn:
        await jobs.finish(conn, job_id, body.ok, port=body.port, admin_url=body.admin_url,
                          setup_url=body.setup_url, error=body.error)
    return Ok()
