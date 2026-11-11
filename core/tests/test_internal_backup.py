from datetime import datetime, timezone

from iler.backup import run as run_mod


async def test_internal_backup_requires_token(client):
    r = await client.post("/internal/backup")
    assert r.status_code == 401
    r = await client.post("/internal/backup", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


async def test_internal_backup_creates_backup(client, monkeypatch):
    info = run_mod.BackupInfo(name="iler-backup-20261111-040000.tar.gz", size=10,
                              created_at=datetime.now(timezone.utc), sha256="ab" * 32)

    async def fake_create():
        return info

    monkeypatch.setattr(run_mod, "create_backup", fake_create)
    monkeypatch.setattr(run_mod, "apply_retention", lambda *a, **k: [])
    r = await client.post("/internal/backup", headers={"Authorization": "Bearer test-updater-token"})
    assert r.status_code == 200
    assert r.json()["name"] == info.name
