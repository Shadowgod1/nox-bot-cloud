from types import SimpleNamespace

import httpx
import pytest

from iler import updater_client
from iler.auth import deps
from iler.backup import run as run_mod
from iler.config import get_settings
from iler.errors import ApiError

from test_backup import FakeCmd


@pytest.fixture
def as_owner(app):
    async def owner():
        return SimpleNamespace(id=None, email="o@example.com", name="Owner", role="owner")

    async def no_csrf():
        return None

    app.dependency_overrides[deps.require_owner] = owner
    app.dependency_overrides[deps.csrf_protect] = no_csrf
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def as_operator(app):
    async def forbidden():
        raise ApiError(403, "forbidden", "Owner only")

    async def no_csrf():
        return None

    app.dependency_overrides[deps.require_owner] = forbidden
    app.dependency_overrides[deps.csrf_protect] = no_csrf
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    monkeypatch.setattr(run_mod, "run_cmd", FakeCmd())
    return tmp_path


class FakeUpdater:
    def __init__(self, fail: bool = False, available: str = "1.1.0"):
        self.requests: list[httpx.Request] = []
        self.fail = fail
        self.available = available

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("refused", request=request)
        if request.headers.get("authorization") != "Bearer test-updater-token":
            return httpx.Response(401, json={"error": "bad token"})
        if request.method == "GET" and request.url.path == "/version":
            return httpx.Response(200, json={"current": "1.0.0", "available": self.available})
        if request.method == "POST" and request.url.path == "/update":
            return httpx.Response(202, json={"job_id": "job-42"})
        if request.method == "GET" and request.url.path == "/update/job-42":
            return httpx.Response(200, json={"state": "pulling", "log": "pulling images"})
        return httpx.Response(404, json={})

    def install(self, app):
        transport = httpx.MockTransport(self.handler)
        settings = get_settings()

        def factory():
            return updater_client.UpdaterClient(settings.updater_url, settings.updater_token, transport=transport)

        app.dependency_overrides[updater_client.get_updater_client] = factory


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/backups"),
        ("post", "/api/backups"),
        ("get", "/api/backups/iler-backup-20261101-040000.tar.gz"),
        ("post", "/api/backups/iler-backup-20261101-040000.tar.gz/restore"),
        ("get", "/api/system/version"),
        ("post", "/api/system/update"),
        ("get", "/api/system/update/status"),
    ],
)
async def test_backup_api_owner_only(client, as_operator, data_dir, method, path):
    r = await client.request(method.upper(), path)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"
    assert not (data_dir / "backups").exists() or not list((data_dir / "backups").iterdir())


async def test_unauthenticated_rejected(client, data_dir):
    # no overrides: the real (or placeholder) auth dependency must reject anonymous calls
    r = await client.get("/api/backups")
    assert r.status_code in (401, 403)


async def test_backup_create_list_download(client, as_owner, data_dir, pool):
    r = await client.post("/api/backups")
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["name"].startswith("iler-backup-") and info["sha256"] and info["size"] > 0

    r = await client.get("/api/backups")
    assert r.status_code == 200
    assert [b["name"] for b in r.json()["items"]] == [info["name"]]

    r = await client.get(f"/api/backups/{info['name']}")
    assert r.status_code == 200
    assert r.content == (data_dir / "backups" / info["name"]).read_bytes()
    assert "attachment" in r.headers["content-disposition"]

    actions = [row["action"] for row in await pool.fetch("SELECT action FROM audit_log ORDER BY id")]
    assert "backup.create" in actions


async def test_backups_list_includes_last_status(client, as_owner, data_dir, pool):
    from iler.backup import status as backup_status

    r = await client.get("/api/backups")
    assert r.json()["last_status"] == {"status": None, "at": None, "message": None}
    await backup_status.record(False, "pg_dump exited 1: disk full")
    body = (await client.get("/api/backups")).json()
    assert body["items"] == []
    assert body["last_status"]["status"] == "error"
    assert body["last_status"]["message"] == "pg_dump exited 1: disk full"
    assert body["last_status"]["at"]
    # a manual backup updates the status as well
    assert (await client.post("/api/backups")).status_code == 200
    assert (await client.get("/api/backups")).json()["last_status"]["status"] == "ok"


async def test_manual_backup_failure_is_recorded(client, as_owner, data_dir, monkeypatch):
    monkeypatch.setattr(run_mod, "run_cmd", FakeCmd(fail_on="pg_dump"))
    r = await client.post("/api/backups")
    assert r.status_code == 500
    last = (await client.get("/api/backups")).json()["last_status"]
    assert last["status"] == "error" and "pg_dump" in last["message"]


async def test_backup_download_missing_404(client, as_owner, data_dir):
    r = await client.get("/api/backups/iler-backup-20200101-000000.tar.gz")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("bad", ["..%2Fx", "..%5Cx", "evil.tar.gz", "iler-backup-1.tar.gz"])
async def test_restore_rejects_path_traversal(client, as_owner, data_dir, bad):
    r = await client.post(f"/api/backups/{bad}/restore")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_backup_name"


async def test_restore_via_api(client, as_owner, data_dir):
    name = (await client.post("/api/backups")).json()["name"]
    r = await client.post(f"/api/backups/{name}/restore")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"] == name
    assert body["redis"] == "skipped"


async def test_update_proxies_to_updater(app, client, as_owner, pool):
    fake = FakeUpdater()
    fake.install(app)

    r = await client.get("/api/system/version")
    assert r.status_code == 200
    body = r.json()
    assert body["current"] == "1.0.0" and body["available"] == "1.1.0"
    assert body["core_version"]

    r = await client.post("/api/system/update")
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == "job-42"

    # status without job_id uses the last started job (persisted, survives a core restart)
    r = await client.get("/api/system/update/status")
    assert r.status_code == 200
    assert r.json() == {"job_id": "job-42", "state": "pulling", "log": "pulling images"}

    assert [(q.method, q.url.path) for q in fake.requests] == [
        ("GET", "/version"),
        ("POST", "/update"),
        ("GET", "/update/job-42"),
    ]
    assert all(q.headers["authorization"] == "Bearer test-updater-token" for q in fake.requests)
    actions = [row["action"] for row in await pool.fetch("SELECT action FROM audit_log ORDER BY id")]
    assert "system.update" in actions


async def test_same_version_is_not_offered_as_an_update(app, client, as_owner):
    FakeUpdater(available="1.0.0").install(app)
    body = (await client.get("/api/system/version")).json()
    assert body["current"] == "1.0.0" and body["available"] is None and body["managed"] is False


async def test_managed_cloud_install_has_no_update(app, client, as_owner, monkeypatch):
    fake = FakeUpdater()
    fake.install(app)
    monkeypatch.setattr(get_settings(), "managed_updates", True)
    body = (await client.get("/api/system/version")).json()
    assert body["managed"] is True and body["available"] is None and body["updater_available"] is False
    r = await client.post("/api/system/update")
    assert r.status_code == 409 and r.json()["error"]["code"] == "managed_updates"
    assert fake.requests == []  # the updater is never asked


async def test_update_status_without_job(app, client, as_owner):
    FakeUpdater().install(app)
    r = await client.get("/api/system/update/status")
    assert r.status_code == 200
    assert r.json() == {"job_id": None, "state": None, "log": ""}


async def test_updater_unavailable(app, client, as_owner):
    FakeUpdater(fail=True).install(app)
    r = await client.get("/api/system/version")
    assert r.status_code == 200
    body = r.json()
    assert body["current"] is None and body["updater_available"] is False and body["core_version"]

    r = await client.post("/api/system/update")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "updater_unavailable"


async def test_updater_client_bearer_async():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"current": "1", "available": None})

    client = updater_client.UpdaterClient("http://u:9000", "tok", transport=httpx.MockTransport(handler))
    assert await client.get_version() == {"current": "1", "available": None}
    assert seen["auth"] == "Bearer tok"
