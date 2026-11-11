import asyncio
import hashlib
import json
import os
import tarfile
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from iler.backup import restore as restore_mod
from iler.backup import run as run_mod
from iler.backup import schedule
from iler.config import get_settings


class FakeCmd:
    """Stands in for pg_dump / redis-cli / pg_restore: records calls, writes output files."""

    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[list[str], dict | None]] = []
        self.fail_on = fail_on

    async def __call__(self, args, env=None):
        self.calls.append((list(args), env))
        prog = os.path.basename(args[0])
        if self.fail_on and prog == self.fail_on:
            raise run_mod.BackupError(f"{prog} failed")
        if prog == "pg_dump":
            out = args[args.index("-f") + 1]
            dbname = next(a for a in args if a.startswith("--dbname="))
            with open(out, "wb") as f:
                f.write(b"PGDMP" + dbname.encode())
        elif prog == "redis-cli":
            out = args[args.index("--rdb") + 1]
            with open(out, "wb") as f:
                f.write(b"REDIS0011-fake")
        return b""


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "database_url", "postgresql://iler:s3cret@postgres:5432/iler")
    return tmp_path


@pytest.fixture
def fake_cmd(monkeypatch):
    fake = FakeCmd()
    monkeypatch.setattr(run_mod, "run_cmd", fake)
    return fake


async def test_create_backup_layout(data_dir, fake_cmd):
    info = await run_mod.create_backup()
    path = data_dir / "backups" / info.name
    assert path.is_file()
    assert info.name.startswith("iler-backup-") and info.name.endswith(".tar.gz")
    assert info.size == path.stat().st_size
    assert info.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    with tarfile.open(path, "r:gz") as tar:
        assert sorted(tar.getnames()) == ["evolution.dump", "iler.dump", "manifest.json", "redis.rdb"]
    # both databases dumped in custom format, password not on the command line
    dumps = [c for c in fake_cmd.calls if c[0][0] == "pg_dump"]
    assert len(dumps) == 2
    for args, env in dumps:
        assert "-Fc" in args
        assert "s3cret" not in " ".join(args)
        assert env and env["PGPASSWORD"] == "s3cret"
    assert {a for args, _ in dumps for a in args if a.startswith("--dbname=")} == {
        "--dbname=postgresql://iler@postgres:5432/iler",
        "--dbname=postgresql://iler@postgres:5432/evolution",
    }
    redis = [c for c in fake_cmd.calls if c[0][0] == "redis-cli"]
    assert redis and "-h" in redis[0][0] and "redis" in redis[0][0]
    # no temp leftovers
    assert [p.name for p in (data_dir / "backups").iterdir()] == [info.name]


async def test_manifest_sha256_matches(data_dir, fake_cmd):
    info = await run_mod.create_backup()
    with tarfile.open(data_dir / "backups" / info.name, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        assert set(manifest["files"]) == {"iler.dump", "evolution.dump", "redis.rdb"}
        for name, meta in manifest["files"].items():
            data = tar.extractfile(name).read()
            assert meta["sha256"] == hashlib.sha256(data).hexdigest()
            assert meta["size"] == len(data)
    assert manifest["version"]
    assert manifest["created_at"]


async def test_failed_dump_leaves_no_archive(data_dir, monkeypatch):
    monkeypatch.setattr(run_mod, "run_cmd", FakeCmd(fail_on="redis-cli"))
    with pytest.raises(run_mod.BackupError):
        await run_mod.create_backup()
    assert list((data_dir / "backups").iterdir()) == []


def _touch(path, age_days: float):
    path.write_bytes(b"x")
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))


def test_retention_keeps_14_days(data_dir):
    d = data_dir / "backups"
    d.mkdir()
    old = d / "iler-backup-20261020-040000.tar.gz"
    older = d / "iler-backup-20261001-040000.tar.gz"
    fresh = d / "iler-backup-20261105-040000.tar.gz"
    edge = d / "iler-backup-20261029-040000.tar.gz"
    foreign = d / "notes.txt"
    _touch(old, 15)
    _touch(older, 40)
    _touch(fresh, 6)
    _touch(edge, 13.9)
    _touch(foreign, 100)
    removed = run_mod.apply_retention()
    assert sorted(removed) == sorted([old.name, older.name])
    assert not old.exists() and not older.exists()
    assert fresh.exists() and edge.exists()
    assert foreign.exists()  # never touch files we did not create


async def test_list_backups_newest_first(data_dir, fake_cmd):
    d = data_dir / "backups"
    d.mkdir()
    _touch(d / "iler-backup-20261101-040000.tar.gz", 10)
    _touch(d / "iler-backup-20261109-040000.tar.gz", 2)
    (d / "junk.tar.gz").write_bytes(b"")
    names = [b.name for b in run_mod.list_backups()]
    assert names == ["iler-backup-20261109-040000.tar.gz", "iler-backup-20261101-040000.tar.gz"]


@pytest.mark.parametrize(
    "bad", ["../x", "..\\x", "/etc/passwd", "iler-backup-20261101-040000.tar.gz/../../x", "x.tar.gz", ""]
)
def test_resolve_rejects_bad_names(data_dir, bad):
    with pytest.raises(run_mod.InvalidBackupName):
        run_mod.resolve_backup(bad)


async def test_restore_runs_pg_restore_and_stages_redis(data_dir, fake_cmd):
    info = await run_mod.create_backup()
    fake_cmd.calls.clear()
    result = await restore_mod.restore_backup(info.name)
    restores = [c for c in fake_cmd.calls if c[0][0] == "pg_restore"]
    assert len(restores) == 2
    for args, env in restores:
        assert "--clean" in args and "--if-exists" in args
        assert "s3cret" not in " ".join(args) and env["PGPASSWORD"] == "s3cret"
    # a safety backup is taken before restoring
    assert result["safety_backup"].startswith("iler-backup-")
    assert (data_dir / "backups" / result["safety_backup"]).exists()
    staged = data_dir / "restore" / "redis.rdb"
    assert staged.read_bytes() == b"REDIS0011-fake"
    assert result["redis"] == "staged"


async def test_restore_rejects_corrupted_archive(data_dir, fake_cmd):
    info = await run_mod.create_backup()
    path = data_dir / "backups" / info.name
    # rewrite the archive with a tampered dump but the original manifest
    tmp = data_dir / "t"
    tmp.mkdir()
    with tarfile.open(path, "r:gz") as tar:
        tar.extractall(tmp, filter="data")
    (tmp / "iler.dump").write_bytes(b"tampered")
    with tarfile.open(path, "w:gz") as tar:
        for n in ("iler.dump", "evolution.dump", "redis.rdb", "manifest.json"):
            tar.add(tmp / n, arcname=n)
    fake_cmd.calls.clear()
    with pytest.raises(run_mod.BackupError):
        await restore_mod.restore_backup(info.name)
    assert not [c for c in fake_cmd.calls if c[0][0] == "pg_restore"]


async def test_restore_missing(data_dir, fake_cmd):
    with pytest.raises(FileNotFoundError):
        await restore_mod.restore_backup("iler-backup-20200101-000000.tar.gz")


# --- schedule / worker -------------------------------------------------------

ALMATY = ZoneInfo("Asia/Almaty")


def test_next_run_same_day_before_four():
    now = datetime(2026, 11, 11, 3, 59, tzinfo=ALMATY)
    assert schedule.next_run(now, ALMATY) == datetime(2026, 11, 11, 4, 0, tzinfo=ALMATY)


def test_next_run_after_four_is_tomorrow():
    now = datetime(2026, 11, 11, 4, 0, tzinfo=ALMATY)
    assert schedule.next_run(now, ALMATY) == datetime(2026, 11, 12, 4, 0, tzinfo=ALMATY)


def test_next_run_converts_from_utc():
    now = datetime(2026, 11, 10, 22, 30, tzinfo=ZoneInfo("UTC"))  # 03:30 in Almaty (UTC+5)
    nxt = schedule.next_run(now, ALMATY)
    assert nxt == datetime(2026, 11, 11, 4, 0, tzinfo=ALMATY)
    assert nxt - now == timedelta(minutes=30)


async def test_scheduler_runs_daily_and_survives_errors(monkeypatch):
    from iler.workers import backup_scheduler

    calls = {"backup": 0, "retention": 0}

    async def flaky_backup():
        calls["backup"] += 1
        if calls["backup"] == 1:
            raise RuntimeError("pg_dump exploded")
        return run_mod.BackupInfo(name="iler-backup-20261111-040000.tar.gz", size=1, created_at=datetime.now(ALMATY))

    def retention():
        calls["retention"] += 1
        return []

    targets: list[datetime] = []

    async def fake_sleep_until(target):
        targets.append(target)
        if calls["backup"] >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(backup_scheduler.run_mod, "create_backup", flaky_backup)
    monkeypatch.setattr(backup_scheduler.run_mod, "apply_retention", retention)
    monkeypatch.setattr(backup_scheduler, "_sleep_until", fake_sleep_until)
    with pytest.raises(asyncio.CancelledError):
        await backup_scheduler.run(None)
    assert calls["backup"] == 3  # kept going after the first failure
    assert calls["retention"] == 2  # retention only after a successful backup
    tz = ZoneInfo(get_settings().tz)
    assert all(t.astimezone(tz).hour == 4 and t.minute == 0 for t in targets)
