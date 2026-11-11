"""ILER updater: update-by-button with backup and automatic rollback (spec section 8).

HTTP contract (Task 3), every call needs `Authorization: Bearer <ILER_UPDATER_TOKEN>`:
    GET  /version          -> {"current": "0.1.0", "available": "0.2.0"}
    POST /update           -> {"job_id": "..."}            (409 if a job is already running)
    GET  /update/{job_id}  -> {"state": queued|backup|pulling|restarting|healthy|rolled_back|failed, "log": "..."}
Unauthenticated: GET /healthz (container healthcheck only).

Flow of a job:
    backup   POST {core}/internal/backup (same bearer token) -> must return 2xx
    pulling  docker compose pull iler-core iler-web iler-updater   (ILER_VERSION=<available>)
    restarting  write ILER_VERSION to .env, docker compose up -d iler-core iler-web
    wait up to 120 s for GET {core}/health == 200 -> healthy (then the updater replaces itself)
    otherwise restore previous ILER_VERSION in .env, up -d again -> rolled_back
The updater listens only on the internal docker network (no published port).
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("iler.updater")

STATES = ("queued", "backup", "pulling", "restarting", "healthy", "rolled_back", "failed")
TERMINAL = ("healthy", "rolled_back", "failed")
# services restarted during an update; iler-updater replaces itself only after success
RESTART_SERVICES = ("iler-core", "iler-web")
PULL_SERVICES = ("iler-core", "iler-web", "iler-updater")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")


class DockerError(Exception):
    pass


class CoreError(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ---- config / env file helpers ------------------------------------------------------------

@dataclass
class Config:
    token: str
    ilerdir: Path
    core_url: str = "http://iler-core:8000"
    channel_url: str = ""
    health_timeout: float = 120.0
    backup_timeout: float = 900.0

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            token=os.environ.get("ILER_UPDATER_TOKEN", ""),
            ilerdir=Path(os.environ.get("ILER_DIR", "/opt/iler")),
            core_url=os.environ.get("ILER_CORE_URL", "http://iler-core:8000").rstrip("/"),
            channel_url=os.environ.get("ILER_CHANNEL_URL", ""),
            health_timeout=float(os.environ.get("ILER_UPDATER_HEALTH_TIMEOUT", "120")),
        )

    @property
    def env_file(self) -> Path:
        return self.ilerdir / ".env"

    @property
    def compose_file(self) -> Path:
        return self.ilerdir / "compose.yml"

    @property
    def jobs_dir(self) -> Path:
        return self.ilerdir / ".updater" / "jobs"


def read_env_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def set_env_value(path: Path, key: str, value: str) -> None:
    """Replace (or append) KEY=value, keeping every other line and the file mode."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, found = [], False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)


def parse_channel(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("ILER_VERSION="):
            value = line.split("=", 1)[1].strip().strip('"')
            return value if VERSION_RE.match(value) else None
    return None


# ---- docker layer (mocked in tests) -------------------------------------------------------

class ComposeOps(Protocol):
    def pull(self, version: str) -> str: ...
    def up(self, version: str) -> str: ...
    def self_update(self, version: str) -> str: ...


class DockerCompose:
    """docker compose CLI against the host engine (docker.sock mounted)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def _base(self) -> list[str]:
        return ["docker", "compose", "--project-directory", str(self.cfg.ilerdir),
                "-f", str(self.cfg.compose_file), "--env-file", str(self.cfg.env_file)]

    def _env(self, version: str) -> dict:
        # Minimal env: process env would override .env values during interpolation.
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG")}
        env["ILER_VERSION"] = version
        return env

    def _run(self, args: list[str], version: str, timeout: int = 1800) -> str:
        cmd = self._base() + args
        try:
            proc = subprocess.run(cmd, env=self._env(version), capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DockerError(f"{' '.join(args)}: {exc}") from exc
        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            raise DockerError(f"{' '.join(args)} exited {proc.returncode}: {output[-2000:]}")
        return output

    def pull(self, version: str) -> str:
        return self._run(["pull", *PULL_SERVICES], version)

    def up(self, version: str) -> str:
        return self._run(["up", "-d", *RESTART_SERVICES], version)

    def self_update(self, version: str) -> str:
        """Recreate iler-updater from a sibling container: we cannot survive replacing ourselves."""
        owner = read_env_value(self.cfg.env_file, "ILER_IMAGE_OWNER") or "shadowgod1"
        image = f"ghcr.io/{owner.lower()}/iler-updater:{version}"
        d = str(self.cfg.ilerdir)
        cmd = ["docker", "run", "-d", "--rm", "--name", f"iler-updater-selfupdate-{uuid.uuid4().hex[:6]}",
               "-v", "/var/run/docker.sock:/var/run/docker.sock", "-v", f"{d}:{d}",
               "-e", f"ILER_VERSION={version}", "--entrypoint", "sh", image, "-c",
               "sleep 5 && docker compose --project-directory \"$0\" -f \"$0/compose.yml\" "
               "--env-file \"$0/.env\" up -d --no-deps iler-updater", d]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise DockerError(f"self-update: {(proc.stdout + proc.stderr).strip()[-1000:]}")
        return proc.stdout.strip()


class CoreOps(Protocol):
    def backup(self) -> dict: ...
    def health(self) -> bool: ...


class CoreClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def backup(self) -> dict:
        try:
            resp = httpx.post(f"{self.cfg.core_url}/internal/backup",
                              headers={"Authorization": f"Bearer {self.cfg.token}"},
                              timeout=self.cfg.backup_timeout)
        except httpx.HTTPError as exc:
            raise CoreError(f"backup request failed: {exc}") from exc
        if resp.status_code // 100 != 2:
            raise CoreError(f"backup returned HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            return resp.json()
        except ValueError:
            return {}

    def health(self) -> bool:
        try:
            return httpx.get(f"{self.cfg.core_url}/health", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False


# ---- jobs ---------------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    target: str
    previous: str
    state: str = "queued"
    lines: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def log(self) -> str:
        return "\n".join(self.lines)

    def add_log(self, msg: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.lines.append(f"{stamp} {msg}")
        log.info("job %s: %s", self.id, msg)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


class Updater:
    def __init__(self, cfg: Config, compose: ComposeOps | None = None, core: CoreOps | None = None,
                 sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.compose = compose or DockerCompose(cfg)
        self.core = core or CoreClient(cfg)
        self.sleep = sleep
        self.monotonic = monotonic
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    # -- versions
    def current_version(self) -> str:
        value = read_env_value(self.cfg.env_file, "ILER_VERSION")
        if not value and (self.cfg.ilerdir / "VERSION").exists():
            value = (self.cfg.ilerdir / "VERSION").read_text(encoding="utf-8").strip()
        return value or "unknown"

    def available_version(self) -> str:
        local = self.cfg.ilerdir / "deploy" / "channel" / "stable.env"
        if self.cfg.channel_url:
            try:
                resp = httpx.get(self.cfg.channel_url, timeout=10, follow_redirects=True)
                if resp.status_code == 200 and (version := parse_channel(resp.text)):
                    local.parent.mkdir(parents=True, exist_ok=True)
                    local.write_text(f"ILER_VERSION={version}\n", encoding="utf-8")
                    return version
                log.warning("channel %s: HTTP %s", self.cfg.channel_url, resp.status_code)
            except httpx.HTTPError as exc:
                log.warning("channel %s unreachable: %s", self.cfg.channel_url, exc)
        if local.exists() and (version := parse_channel(local.read_text(encoding="utf-8"))):
            return version
        return self.current_version()

    # -- job storage
    def _save(self, job: Job) -> None:
        self.cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
        path = self.cfg.jobs_dir / f"{job.id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict()), encoding="utf-8")
        os.replace(tmp, path)

    def get_job(self, job_id: str) -> Job | None:
        if job_id in self.jobs:
            return self.jobs[job_id]
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            return None
        path = self.cfg.jobs_dir / f"{job_id}.json"
        if path.exists():
            return Job.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return None

    def _set_state(self, job: Job, state: str, msg: str | None = None) -> None:
        assert state in STATES
        job.state = state
        if msg:
            job.add_log(msg)
        if state in TERMINAL:
            job.finished_at = time.time()
        self._save(job)

    def new_job(self) -> Job:
        with self.lock:
            if any(j.state not in TERMINAL for j in self.jobs.values()):
                raise ApiError(409, "update_in_progress", "an update is already running")
            job = Job(id=uuid.uuid4().hex, target=self.available_version(), previous=self.current_version())
            self.jobs[job.id] = job
            job.add_log(f"update requested: {job.previous} -> {job.target}")
            self._save(job)
            return job

    def start_job(self, job: Job) -> None:
        threading.Thread(target=self.run_job, args=(job,), daemon=True, name=f"update-{job.id[:8]}").start()

    # -- the update itself
    def _wait_healthy(self, job: Job) -> bool:
        deadline = self.monotonic() + self.cfg.health_timeout
        while True:
            if self.core.health():
                return True
            if self.monotonic() >= deadline:
                return False
            self.sleep(3)

    def run_job(self, job: Job) -> None:
        try:
            self._run(job)
        except Exception as exc:  # never leave a job hanging in a non-terminal state
            log.exception("update job crashed")
            self._set_state(job, "failed", f"unexpected error: {exc}")

    def _run(self, job: Job) -> None:
        if not VERSION_RE.match(job.target):
            self._set_state(job, "failed", f"invalid target version {job.target!r}")
            return

        self._set_state(job, "backup", "creating backup via core")
        try:
            info = self.core.backup()
        except CoreError as exc:
            self._set_state(job, "failed", f"backup failed, nothing changed: {exc}")
            return
        job.add_log(f"backup done: {info.get('name', '?')}")

        self._set_state(job, "pulling", f"pulling images {job.target}")
        try:
            self.compose.pull(job.target)
        except DockerError as exc:
            self._set_state(job, "failed", f"pull failed, nothing changed: {exc}")
            return

        self._set_state(job, "restarting", "restarting services")
        set_env_value(self.cfg.env_file, "ILER_VERSION", job.target)
        try:
            self.compose.up(job.target)
            healthy = self._wait_healthy(job)
            if not healthy:
                job.add_log(f"core not healthy after {self.cfg.health_timeout:.0f}s")
        except DockerError as exc:
            job.add_log(f"up failed: {exc}")
            healthy = False

        if healthy:
            (self.cfg.ilerdir / "VERSION").write_text(job.target + "\n", encoding="utf-8")
            self._set_state(job, "healthy", f"version {job.target} is up")
            if job.target != job.previous:
                try:
                    self.compose.self_update(job.target)
                    job.add_log("updater self-update scheduled")
                except DockerError as exc:
                    job.add_log(f"updater self-update skipped: {exc}")
                self._save(job)
            return

        job.add_log(f"rolling back to {job.previous}")
        set_env_value(self.cfg.env_file, "ILER_VERSION", job.previous)
        try:
            self.compose.up(job.previous)
        except DockerError as exc:
            self._set_state(job, "failed", f"rollback failed: {exc}")
            return
        if self._wait_healthy(job):
            self._set_state(job, "rolled_back", f"rolled back to {job.previous}")
        else:
            self._set_state(job, "failed", f"rolled back to {job.previous} but core is still unhealthy")


# ---- HTTP ---------------------------------------------------------------------------------

def create_app(updater: Updater | None = None) -> FastAPI:
    upd = updater or Updater(Config.from_env())
    api = FastAPI(title="iler-updater", docs_url=None, redoc_url=None, openapi_url=None)

    @api.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})

    def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, given = header.partition(" ")
        expected = upd.cfg.token
        if not expected or scheme.lower() != "bearer" or not secrets.compare_digest(given.encode(), expected.encode()):
            raise ApiError(401, "unauthorized", "missing or invalid bearer token")

    @api.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @api.get("/version", dependencies=[Depends(require_token)])
    def version() -> dict:
        return {"current": upd.current_version(), "available": upd.available_version()}

    @api.post("/update", dependencies=[Depends(require_token)])
    def update() -> dict:
        job = upd.new_job()
        upd.start_job(job)
        return {"job_id": job.id}

    @api.get("/update/{job_id}", dependencies=[Depends(require_token)])
    def update_status(job_id: str) -> dict:
        job = upd.get_job(job_id)
        if job is None:
            raise ApiError(404, "not_found", "unknown job")
        return {"state": job.state, "log": job.log, "target": job.target, "previous": job.previous}

    return api


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
