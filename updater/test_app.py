"""Updater tests with a mocked docker layer and a mocked core client."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as updater_app
from app import Config, Job, Updater, create_app

TOKEN = "t0ken"


class FakeCompose:
    def __init__(self, calls: list, fail_pull: bool = False):
        self.calls = calls
        self.fail_pull = fail_pull
        self.digests: list = []

    def pull(self, version: str, digests=None) -> str:
        self.calls.append(("pull", version))
        self.digests.append(("pull", digests))
        if self.fail_pull:
            raise updater_app.DockerError("pull failed")
        return "pulled"

    def up(self, version: str, digests=None) -> str:
        self.calls.append(("up", version))
        self.digests.append(("up", digests))
        return "up"

    def self_update(self, version: str, digests=None) -> str:
        self.calls.append(("self_update", version))
        return "scheduled"


class FakeCore:
    def __init__(self, calls: list, healthy: bool = True, backup_ok: bool = True):
        self.calls = calls
        self.healthy = healthy
        self.backup_ok = backup_ok

    def backup(self) -> dict:
        self.calls.append(("backup",))
        if not self.backup_ok:
            raise updater_app.CoreError("backup failed")
        return {"name": "iler-backup-20261111-000000.tar.gz"}

    def health(self) -> bool:
        self.calls.append(("health",))
        if self.healthy:
            return True
        # the new version never becomes healthy; the previous one (after rollback) does
        last_up = [c for c in self.calls if c[0] == "up"][-1]
        return last_up[1] == "0.1.0"


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def ilerdir(tmp_path: Path) -> Path:
    (tmp_path / "deploy" / "channel").mkdir(parents=True)
    (tmp_path / ".env").write_text("ILER_SECRET_KEY=x\nILER_VERSION=0.1.0\nILER_HTTP_PORT=8080\n")
    (tmp_path / "deploy" / "channel" / "stable.env").write_text("ILER_VERSION=0.2.0\n")
    (tmp_path / "compose.yml").write_text("name: iler\n")
    return tmp_path


def make_updater(ilerdir: Path, calls: list, **core_kwargs) -> tuple[Updater, FakeClock]:
    clock = FakeClock()
    cfg = Config(token=TOKEN, ilerdir=ilerdir, core_url="http://core", channel_url="", health_timeout=120)
    upd = Updater(cfg, compose=FakeCompose(calls), core=FakeCore(calls, **core_kwargs),
                  sleep=clock.sleep, monotonic=clock.monotonic)
    return upd, clock


def test_rejects_without_token(ilerdir):
    upd, _ = make_updater(ilerdir, [])
    client = TestClient(create_app(upd))
    assert client.get("/version").status_code == 401
    assert client.post("/update").status_code == 401
    assert client.get("/update/abc").status_code == 401
    assert client.get("/version", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.post("/update", headers={"Authorization": "Basic " + TOKEN})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    ok = client.get("/version", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200
    assert ok.json() == {"current": "0.1.0", "available": "0.2.0"}


def test_empty_configured_token_rejects_everything(ilerdir):
    upd, _ = make_updater(ilerdir, [])
    upd.cfg.token = ""
    client = TestClient(create_app(upd))
    assert client.get("/version", headers={"Authorization": "Bearer "}).status_code == 401


def test_backup_called_before_pull(ilerdir):
    calls: list = []
    upd, _ = make_updater(ilerdir, calls)
    job = upd.new_job()
    upd.run_job(job)
    names = [c[0] for c in calls]
    assert names.index("backup") < names.index("pull") < names.index("up")
    assert job.state == "healthy"
    assert ("pull", "0.2.0") in calls
    assert "ILER_VERSION=0.2.0" in (ilerdir / ".env").read_text()
    assert (ilerdir / "VERSION").read_text().strip() == "0.2.0"


def test_backup_failure_stops_before_pull(ilerdir):
    calls: list = []
    upd, _ = make_updater(ilerdir, calls, backup_ok=False)
    job = upd.new_job()
    upd.run_job(job)
    assert job.state == "failed"
    assert not any(c[0] == "pull" for c in calls)
    assert "ILER_VERSION=0.1.0" in (ilerdir / ".env").read_text()


def test_rollback_on_unhealthy(ilerdir):
    calls: list = []
    upd, clock = make_updater(ilerdir, calls, healthy=False)
    job = upd.new_job()
    upd.run_job(job)
    assert job.state == "rolled_back"
    ups = [c for c in calls if c[0] == "up"]
    assert ups == [("up", "0.2.0"), ("up", "0.1.0")]
    assert clock.now >= 120  # waited the full health timeout before giving up
    env = (ilerdir / ".env").read_text()
    assert "ILER_VERSION=0.1.0" in env and "ILER_VERSION=0.2.0" not in env
    assert "ILER_SECRET_KEY=x" in env  # other keys untouched
    assert not any(c[0] == "self_update" for c in calls)


def test_rollback_still_unhealthy_is_failed(ilerdir):
    calls: list = []
    upd, _ = make_updater(ilerdir, calls, healthy=False)
    upd.core.health = lambda: False
    job = upd.new_job()
    upd.run_job(job)
    assert job.state == "failed"
    assert "ILER_VERSION=0.1.0" in (ilerdir / ".env").read_text()


def test_job_status_endpoint_and_persistence(ilerdir):
    calls: list = []
    upd, _ = make_updater(ilerdir, calls)
    upd.start_job = lambda job: upd.run_job(job)  # run synchronously
    client = TestClient(create_app(upd))
    h = {"Authorization": f"Bearer {TOKEN}"}
    job_id = client.post("/update", headers=h).json()["job_id"]
    status = client.get(f"/update/{job_id}", headers=h).json()
    assert status["state"] == "healthy"
    assert isinstance(status["log"], str) and "backup" in status["log"]
    # a fresh updater (e.g. after self-update restart) still knows the job
    upd2, _ = make_updater(ilerdir, [])
    client2 = TestClient(create_app(upd2))
    assert client2.get(f"/update/{job_id}", headers=h).json()["state"] == "healthy"
    assert client2.get("/update/nope", headers=h).status_code == 404


def test_second_update_while_running_conflicts(ilerdir):
    upd, _ = make_updater(ilerdir, [])
    upd.start_job = lambda job: None  # leave it queued
    client = TestClient(create_app(upd))
    h = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/update", headers=h).status_code == 200
    assert client.post("/update", headers=h).status_code == 409


def test_job_keeps_pre_update_backup_name(ilerdir):
    upd, _ = make_updater(ilerdir, [])
    upd.start_job = lambda job: upd.run_job(job)
    client = TestClient(create_app(upd))
    h = {"Authorization": f"Bearer {TOKEN}"}
    job_id = client.post("/update", headers=h).json()["job_id"]
    assert client.get(f"/update/{job_id}", headers=h).json()["backup"] == "iler-backup-20261111-000000.tar.gz"
    # persisted: survives an updater restart
    upd2, _ = make_updater(ilerdir, [])
    status = TestClient(create_app(upd2)).get(f"/update/{job_id}", headers=h).json()
    assert status["backup"] == "iler-backup-20261111-000000.tar.gz"


def test_failed_backup_has_no_backup_name(ilerdir):
    upd, _ = make_updater(ilerdir, [], backup_ok=False)
    job = upd.new_job()
    upd.run_job(job)
    assert job.backup is None


D1 = "sha256:" + "a" * 64
D2 = "sha256:" + "b" * 64


def test_parse_channel_entries_validates_digests():
    text = f"ILER_VERSION=0.2.0\nILER_CORE_DIGEST={D1}\nILER_WEB_DIGEST=sha256:short\nILER_EVIL=1\n"
    assert updater_app.parse_channel_entries(text) == {"ILER_VERSION": "0.2.0", "ILER_CORE_DIGEST": D1}
    assert updater_app.parse_channel_entries(f"ILER_CORE_DIGEST={D1}\n") == {}


def test_update_pins_digests_from_channel_and_rolls_them_back(ilerdir):
    (ilerdir / "deploy" / "channel" / "stable.env").write_text(
        f"ILER_VERSION=0.2.0\nILER_CORE_DIGEST={D1}\nILER_WEB_DIGEST={D2}\n")
    calls: list = []
    upd, _ = make_updater(ilerdir, calls)
    job = upd.new_job()
    upd.run_job(job)
    assert job.state == "healthy"
    assert upd.compose.digests[0] == ("pull", {"ILER_CORE_DIGEST": D1, "ILER_WEB_DIGEST": D2})
    env = (ilerdir / ".env").read_text()
    assert f"ILER_CORE_DIGEST={D1}" in env and f"ILER_WEB_DIGEST={D2}" in env

    # next release without digests on a broken build: rollback restores the previous pins
    (ilerdir / "deploy" / "channel" / "stable.env").write_text("ILER_VERSION=0.3.0\n")
    calls2: list = []
    upd2, _ = make_updater(ilerdir, calls2, healthy=False)
    upd2.core.health = lambda: [c for c in calls2 if c[0] == "up"][-1][1] == "0.2.0"
    job2 = upd2.new_job()
    upd2.run_job(job2)
    assert job2.state == "rolled_back"
    assert upd2.compose.digests[0] == ("pull", {})
    env = (ilerdir / ".env").read_text()
    assert "ILER_VERSION=0.2.0" in env and f"ILER_CORE_DIGEST={D1}" in env


def test_job_dataclass_roundtrip():
    job = Job(id="x", target="0.2.0", previous="0.1.0")
    job.add_log("hello")
    assert Job.from_dict(job.to_dict()).log == job.log
