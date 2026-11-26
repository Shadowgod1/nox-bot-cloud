"""Unit tests for deploy/cloud/nox-provisioner.py: ingress merge, Cloudflare calls and the job loop, with
stubbed HTTP and a fake iler-client.sh (no network, no real tokens, no docker).

    python -m pytest -q deploy/cloud/tests
"""

import base64
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "nox-provisioner.py"
_spec = importlib.util.spec_from_file_location("nox_provisioner", _PATH)
np = importlib.util.module_from_spec(_spec)
sys.modules["nox_provisioner"] = np
_spec.loader.exec_module(np)

TUNNEL = "11111111-2222-3333-4444-555555555555"
CATCH = {"service": "http_status:404"}
FAKE_CF_TOKEN = "cf-test-token-not-real"


# ---- ingress merge --------------------------------------------------------------------------


def test_merge_into_empty_adds_catch_all_last():
    rules, changed = np.merge_ingress([], "acme.example.kz", "http://127.0.0.1:8110")
    assert changed
    assert rules == [{"hostname": "acme.example.kz", "service": "http://127.0.0.1:8110"}, CATCH]


def test_merge_preserves_existing_rules_and_order():
    existing = [
        {"hostname": "bot.example.kz", "service": "http://127.0.0.1:8100", "originRequest": {"noTLSVerify": False}},
        {"hostname": "old.example.kz", "path": "/api", "service": "http://127.0.0.1:9000"},
        {"service": "http_status:404"},
    ]
    rules, changed = np.merge_ingress(existing, "acme.example.kz", "http://127.0.0.1:8110")
    assert changed
    assert rules[:2] == existing[:2]
    assert rules[2] == {"hostname": "acme.example.kz", "service": "http://127.0.0.1:8110"}
    assert rules[-1] == CATCH


def test_merge_is_idempotent():
    first, _ = np.merge_ingress([CATCH], "acme.example.kz", "http://127.0.0.1:8110")
    second, changed = np.merge_ingress(first, "ACME.example.kz", "http://127.0.0.1:8110")
    assert not changed and second == first


def test_merge_replaces_stale_rule_for_same_host():
    existing = [{"hostname": "acme.example.kz", "service": "http://127.0.0.1:8101"}, CATCH]
    rules, changed = np.merge_ingress(existing, "acme.example.kz", "http://127.0.0.1:8110")
    assert changed
    assert rules == [{"hostname": "acme.example.kz", "service": "http://127.0.0.1:8110"}, CATCH]


def test_merge_keeps_custom_catch_all_and_moves_it_last():
    existing = [{"service": "http_status:503"}, {"hostname": "x.example.kz", "service": "http://127.0.0.1:1"}]
    rules, _ = np.merge_ingress(existing, "acme.example.kz", "http://127.0.0.1:8110")
    assert np.is_catch_all(rules[-1])
    assert rules[-2]["hostname"] == "acme.example.kz"
    # a hostname-less rule that is not last is left where it was (Cloudflare would reject it anyway)
    assert {"service": "http_status:503"} in rules


def test_merge_keeps_path_rules_for_same_host():
    existing = [{"hostname": "acme.example.kz", "path": "/hook", "service": "http://127.0.0.1:7000"}, CATCH]
    rules, _ = np.merge_ingress(existing, "acme.example.kz", "http://127.0.0.1:8110")
    assert rules[0] == existing[0]
    assert rules[1] == {"hostname": "acme.example.kz", "service": "http://127.0.0.1:8110"}


def test_drop_ingress():
    existing = [{"hostname": "a.example.kz", "service": "s1"}, {"hostname": "acme.example.kz", "service": "s2"}, CATCH]
    rules, changed = np.drop_ingress(existing, "acme.example.kz")
    assert changed and rules == [existing[0], CATCH]
    again, changed = np.drop_ingress(rules, "acme.example.kz")
    assert not changed and again == rules


# ---- fakes ----------------------------------------------------------------------------------


class FakeHttp:
    """Portal internal API + Cloudflare API in memory."""

    def __init__(self, jobs=None, ingress=None, records=None):
        self.jobs = list(jobs or [])
        self.reports = {}
        self.config = {"ingress": list(ingress if ingress is not None else [CATCH]), "warp-routing": {"enabled": False}}
        self.records = list(records or [])
        self.calls = []
        self.fail_put = False

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.startswith("http://127.0.0.1:8099"):
            assert headers.get("X-Nox-Provisioner-Token") == "prov-token"
            path = url.split("8099", 1)[1]
            if path == "/internal/ping":
                return 200, {"ok": True}
            if path == "/internal/jobs/claim":
                return (200, self.jobs.pop(0)) if self.jobs else (204, None)
            job_id = int(path.split("/")[3])
            self.reports[job_id] = body
            return 200, {"ok": True}
        assert url.startswith("https://cf.test/client/v4"), url
        assert headers["Authorization"] == f"Bearer {FAKE_CF_TOKEN}"
        path = url.split("/client/v4", 1)[1]
        if path.endswith("/configurations"):
            assert f"/accounts/acc123/cfd_tunnel/{TUNNEL}/" in path
            if method == "GET":
                return 200, {"success": True, "result": {"config": self.config, "version": 3}}
            if self.fail_put:
                return 400, {"success": False, "errors": [{"code": 1055, "message": "bad ingress"}]}
            assert list(body) == ["config"]
            self.config = body["config"]
            return 200, {"success": True, "result": {"config": self.config, "version": 4}}
        assert path.startswith("/zones/zone123/dns_records")
        if method == "GET":
            name = path.split("name.exact=")[1].split("&")[0]
            return 200, {"success": True, "result": [r for r in self.records if r["name"] == name]}
        if method == "POST":
            rec = {**body, "id": f"rec{len(self.records) + 1}"}
            self.records.append(rec)
            return 200, {"success": True, "result": rec}
        rec_id = path.rsplit("/", 1)[1]
        if method == "PATCH":
            for r in self.records:
                if r["id"] == rec_id:
                    r.update(body)
            return 200, {"success": True, "result": {"id": rec_id}}
        if method == "DELETE":
            self.records = [r for r in self.records if r["id"] != rec_id]
            return 200, {"success": True, "result": {"id": rec_id}}
        raise AssertionError((method, url))


class FakeIlerClient:
    """Plays iler-client.sh / set-llm-keys.sh: create writes <dir>/.env like the real script + install.sh."""

    def __init__(self, clients_dir: Path, brand=True):
        self.dir = clients_dir
        self.brand = brand
        self.commands = []
        self.envs = []
        self.fail = set()  # subcommands that fail

    def __call__(self, cmd, env):
        cmd = [str(c) for c in cmd]
        self.commands.append(cmd)
        self.envs.append(env)
        if cmd[1].endswith("set-llm-keys.sh"):
            if "llm" in self.fail:
                return 1, "error: ILER_ANTHROPIC_KEY=sk-ant-api03-secret rejected"
            env_file = Path(cmd[3]) / ".env"
            env_file.write_text(env_file.read_text() + f"ILER_ANTHROPIC_KEY={env['ILER_ANTHROPIC_KEY']}\n")
            return 0, "ok Anthropic (Claude): ****abcd"
        sub = cmd[2]
        if sub == "--help":
            return 0, "create <name> --port N" + (" [--brand nox|iler]" if self.brand else "")
        if sub in self.fail:
            if sub == "create":
                (self.dir / cmd[3]).mkdir(parents=True, exist_ok=True)
            return 1, "==> creating\nsetup link: https://x.example.kz/setup?token=deadbeefsecret\nerror: boom"
        if sub == "create":
            slug = cmd[3]
            args = dict(zip(cmd[4::2], cmd[5::2]))
            d = self.dir / slug
            (d / "deploy").mkdir(parents=True)
            (d / ".env").write_text(
                f"ILER_PROJECT=iler-{slug}\nILER_HTTP_BIND=127.0.0.1\nILER_HTTP_PORT={args['--port']}\n"
                f"ILER_PUBLIC_URL={args['--public-url']}\nILER_MONTHLY_COINS={args.get('--coins', 0)}\n"
                "ILER_SETUP_TOKEN=0123456789abcdef0123456789abcdef0123456789abcdef\n"
            )
            (d / "deploy" / "set-llm-keys.sh").write_text("#!/bin/sh\n")
            return 0, f"ok client created\n  {args['--public-url']}/setup?token=0123456789abcdef"
        if sub == "remove":
            shutil.rmtree(self.dir / cmd[3], ignore_errors=True)
            return 0, "ok removed"
        if sub in ("stop", "start"):
            return 0, f"ok {sub}"
        raise AssertionError(cmd)


@pytest.fixture
def env(tmp_path):
    clients = tmp_path / "clients"
    clients.mkdir()
    iler = tmp_path / "bundle" / "deploy" / "cloud" / "iler-client.sh"
    iler.parent.mkdir(parents=True)
    iler.write_text("#!/bin/sh\n")
    cfg = np.Config.from_env({
        "NOX_PROVISIONER_TOKEN": "prov-token", "NOX_ILER_CLIENT": str(iler), "NOX_CLIENTS_DIR": str(clients),
        "NOX_DOMAIN": "example.kz", "NOX_PORT_MIN": "8110", "NOX_PORT_MAX": "8115",
        "NOX_DEFAULT_ANTHROPIC_KEY": "sk-ant-api03-default-key-xxxxxxxxxxxx",
        "CF_API_TOKEN": FAKE_CF_TOKEN, "CF_ACCOUNT_ID": "acc123", "CF_TUNNEL_ID": TUNNEL, "CF_ZONE_ID": "zone123",
        "CF_API_URL": "https://cf.test/client/v4",
    })
    return cfg, clients


def make(cfg, http, runner, listening=lambda p: False, setup_state=lambda p: True,
         unseal_fn=lambda blob: "sk-ant-api03-per-client-key-yyyyyyyy"):
    return np.Provisioner(cfg, np.Portal(cfg, http), np.Cloudflare(cfg, http), run=runner, listening=listening,
                          unseal_fn=unseal_fn, setup_state=setup_state)


def create_job(job_id=1, slug="acme", sealed=None, coins=30000):
    return {"id": job_id, "kind": "create", "slug": slug, "coins": coins,
            "public_url": f"https://{slug}.example.kz", "llm_key_sealed": sealed}


# ---- config ---------------------------------------------------------------------------------


def test_config_problems(env):
    cfg, _ = env
    assert cfg.problems() == []
    bad = np.Config.from_env({"NOX_PORTAL_INTERNAL_URL": "http://bot.example.kz:8099"})
    problems = "\n".join(bad.problems())
    assert "NOX_PROVISIONER_TOKEN" in problems and "CF_API_TOKEN" in problems and "127.0.0.1" in problems


# ---- job loop -------------------------------------------------------------------------------


def test_create_job_end_to_end(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job()], ingress=[{"hostname": "bot.example.kz", "service": "http://127.0.0.1:8100"},
                                                 CATCH])
    runner = FakeIlerClient(clients)
    p = make(cfg, http, runner, listening=lambda port: port == 8110)
    assert p.run_once() == 1
    report = http.reports[1]
    assert report == {"ok": True, "port": 8111, "admin_url": "https://acme.example.kz",
                      "setup_url": "https://acme.example.kz/setup?token=0123456789abcdef0123456789abcdef0123456789abcdef"}
    create = next(c for c in runner.commands if c[2] == "create")
    assert create[3:] == ["acme", "--port", "8111", "--public-url", "https://acme.example.kz", "--coins", "30000",
                          "--brand", "nox"]
    # key went through the environment of set-llm-keys.sh, never argv
    llm_idx = next(i for i, c in enumerate(runner.commands) if c[1].endswith("set-llm-keys.sh"))
    assert runner.commands[llm_idx][2:] == ["--dir", str(clients / "acme"), "--yes"]
    assert runner.envs[llm_idx] == {"ILER_ANTHROPIC_KEY": "sk-ant-api03-default-key-xxxxxxxxxxxx",
                                    "ILER_MONTHLY_COINS": ""}
    assert not any("sk-ant" in " ".join(c) for c in runner.commands)
    # tunnel: existing rule kept, new one before the catch-all, other config keys preserved
    assert http.config["ingress"] == [{"hostname": "bot.example.kz", "service": "http://127.0.0.1:8100"},
                                      {"hostname": "acme.example.kz", "service": "http://127.0.0.1:8111"}, CATCH]
    assert http.config["warp-routing"] == {"enabled": False}
    assert http.records == [{"type": "CNAME", "name": "acme.example.kz", "content": f"{TUNNEL}.cfargotunnel.com",
                             "ttl": 1, "proxied": True, "comment": "Nox bot (nox-provisioner)", "id": "rec1"}]


def test_create_with_per_client_key(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job(sealed="c2VhbGVk")])
    runner = FakeIlerClient(clients)
    seen = []
    p = make(cfg, http, runner, unseal_fn=lambda blob: seen.append(blob) or "sk-ant-api03-per-client-key-yyyyyyyy")
    p.run_once()
    assert seen == ["c2VhbGVk"]
    llm_env = next(e for c, e in zip(runner.commands, runner.envs) if c[1].endswith("set-llm-keys.sh"))
    assert llm_env["ILER_ANTHROPIC_KEY"] == "sk-ant-api03-per-client-key-yyyyyyyy"
    assert http.reports[1]["ok"] is True


def test_create_without_brand_support_and_without_coins(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job(coins=None)])
    runner = FakeIlerClient(clients, brand=False)
    make(cfg, http, runner).run_once()
    create = next(c for c in runner.commands if c[2] == "create")
    assert "--brand" not in create and "--coins" not in create


def test_create_failure_cleans_up_and_redacts(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job()])
    runner = FakeIlerClient(clients)
    runner.fail.add("create")
    make(cfg, http, runner).run_once()
    report = http.reports[1]
    assert report["ok"] is False
    assert "boom" in report["error"]
    assert "deadbeefsecret" not in report["error"]
    assert any(c[2:] == ["remove", "acme", "--yes"] for c in runner.commands), "partial instance removed"
    assert not (clients / "acme").exists()
    assert http.config["ingress"] == [CATCH], "no tunnel change on failure"


def test_llm_key_failure_is_reported_and_retry_reuses_instance(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job()])
    runner = FakeIlerClient(clients)
    runner.fail.add("llm")
    make(cfg, http, runner).run_once()
    assert http.reports[1]["ok"] is False
    assert "sk-ant-api03-secret" not in http.reports[1]["error"]
    assert (clients / "acme" / ".env").is_file(), "installed instance is kept for the retry"
    # retry: same slug, the instance exists -> no second create, key and tunnel are done now
    runner.fail.clear()
    http.jobs.append(create_job(job_id=2))
    make(cfg, http, runner).run_once()
    assert http.reports[2]["ok"] is True and http.reports[2]["port"] == 8110
    assert sum(1 for c in runner.commands if c[2] == "create") == 1
    assert any(c[2:] == ["start", "acme"] for c in runner.commands)


def test_existing_instance_with_other_settings_is_refused(env):
    cfg, clients = env
    (clients / "acme").mkdir()
    (clients / "acme" / ".env").write_text("ILER_PROJECT=iler-acme\nILER_PUBLIC_URL=https://other.example.kz\n")
    http = FakeHttp(jobs=[create_job()])
    make(cfg, http, FakeIlerClient(clients)).run_once()
    assert http.reports[1]["ok"] is False and "other settings" in http.reports[1]["error"]


def test_no_setup_link_when_owner_exists(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job()])
    make(cfg, http, FakeIlerClient(clients), setup_state=lambda p: False).run_once()
    assert http.reports[1]["ok"] is True and http.reports[1]["setup_url"] is None


def test_port_allocation_skips_used_and_reserved(env):
    cfg, clients = env
    for name, port in (("one", 8110), ("two", 8112)):
        (clients / name).mkdir()
        (clients / name / ".env").write_text(f"ILER_HTTP_PORT={port}\n")
    p = make(cfg, FakeHttp(), FakeIlerClient(clients), listening=lambda port: port == 8111)
    assert p.pick_port() == 8113
    p = make(cfg, FakeHttp(), FakeIlerClient(clients), listening=lambda port: True)
    with pytest.raises(np.ProvisionError):
        p.pick_port()


def test_missing_default_key_fails(env):
    cfg, clients = env
    cfg.default_key = ""
    http = FakeHttp(jobs=[create_job()])
    make(cfg, http, FakeIlerClient(clients)).run_once()
    assert http.reports[1]["ok"] is False and "NOX_DEFAULT_ANTHROPIC_KEY" in http.reports[1]["error"]


def test_dns_conflict_is_not_overwritten(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job()], records=[{"id": "x1", "type": "A", "name": "acme.example.kz",
                                                   "content": "203.0.113.10", "proxied": False}])
    make(cfg, http, FakeIlerClient(clients)).run_once()
    assert http.reports[1]["ok"] is False and "do not point to the tunnel" in http.reports[1]["error"]
    assert http.records[0]["content"] == "203.0.113.10"


def test_dns_existing_unproxied_cname_gets_proxied(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job()], records=[{"id": "x1", "type": "CNAME", "name": "acme.example.kz",
                                                   "content": f"{TUNNEL}.cfargotunnel.com", "proxied": False}])
    make(cfg, http, FakeIlerClient(clients)).run_once()
    assert http.reports[1]["ok"] is True
    assert http.records == [{"id": "x1", "type": "CNAME", "name": "acme.example.kz",
                             "content": f"{TUNNEL}.cfargotunnel.com", "proxied": True}]


def test_cloudflare_error_is_reported_without_token(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job()])
    http.fail_put = True
    make(cfg, http, FakeIlerClient(clients)).run_once()
    err = http.reports[1]["error"]
    assert "1055" in err and FAKE_CF_TOKEN not in err


def test_suspend_resume_remove(env):
    cfg, clients = env
    http = FakeHttp(jobs=[create_job()])
    runner = FakeIlerClient(clients)
    make(cfg, http, runner).run_once()
    http.jobs += [{"id": 2, "kind": "suspend", "slug": "acme"}, {"id": 3, "kind": "resume", "slug": "acme"},
                  {"id": 4, "kind": "remove", "slug": "acme"}]
    assert make(cfg, http, runner).run_once() == 3
    assert [http.reports[i]["ok"] for i in (2, 3, 4)] == [True, True, True]
    subs = [c[2:4] for c in runner.commands if c[2] in ("stop", "start", "remove")]
    assert subs == [["stop", "acme"], ["start", "acme"], ["remove", "acme"]]
    assert http.config["ingress"] == [CATCH]
    assert http.records == []
    # remove again: idempotent (no instance, no route)
    http.jobs.append({"id": 5, "kind": "remove", "slug": "acme"})
    make(cfg, http, runner).run_once()
    assert http.reports[5]["ok"] is True


def test_invalid_jobs_are_rejected(env):
    cfg, clients = env
    http = FakeHttp(jobs=[{"id": 7, "kind": "create", "slug": "../etc", "public_url": "x"},
                          {"id": 8, "kind": "explode", "slug": "acme"},
                          {"id": 9, "kind": "create", "slug": "acme", "public_url": "https://evil.example.com"}])
    runner = FakeIlerClient(clients)
    assert make(cfg, http, runner).run_once() == 3
    assert [http.reports[i]["ok"] for i in (7, 8, 9)] == [False, False, False]
    assert not [c for c in runner.commands if c[2] == "create"]


def test_max_jobs_per_run(env):
    cfg, clients = env
    cfg.max_jobs = 2
    http = FakeHttp(jobs=[{"id": i, "kind": "suspend", "slug": "acme"} for i in range(1, 5)])
    assert make(cfg, http, FakeIlerClient(clients)).run_once() == 2
    assert len(http.jobs) == 2


def test_child_env_drops_secrets(monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "x")
    monkeypatch.setenv("NOX_PROVISIONER_TOKEN", "y")
    monkeypatch.setenv("NOX_DEFAULT_ANTHROPIC_KEY", "z")
    env = np.child_env({"ILER_ANTHROPIC_KEY": "k"})
    assert "CF_API_TOKEN" not in env and "NOX_PROVISIONER_TOKEN" not in env and "NOX_DEFAULT_ANTHROPIC_KEY" not in env
    assert env["ILER_ANTHROPIC_KEY"] == "k"


def test_redact():
    text = ("ILER_ANTHROPIC_KEY=sk-ant-api03-abc https://a.example.kz/setup?token=abc123 "
            "Authorization: Bearer xyz CF_API_TOKEN=tok")
    out = np.redact(text)
    for secret in ("sk-ant-api03-abc", "abc123", "xyz", "=tok"):
        assert secret not in out


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl CLI not available")
def test_unseal_roundtrip_with_openssl(tmp_path):
    crypto = pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    assert crypto
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "key.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    secret = "sk-ant-api03-" + "Z" * 40
    blob = base64.b64encode(key.public_key().encrypt(
        secret.encode(), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )).decode()
    assert np.unseal(blob, str(pem)) == secret
    with pytest.raises(np.ProvisionError):
        np.unseal("not base64!!", str(pem))
