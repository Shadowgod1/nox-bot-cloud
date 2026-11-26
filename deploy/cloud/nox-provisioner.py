#!/usr/bin/env python3
"""Nox provisioner: turns approved portal applications into running bot instances (host side, root).

  python3 nox-provisioner.py once     # claim and process queued jobs, then exit (systemd timer)
  python3 nox-provisioner.py check    # validate config, reach the portal and Cloudflare; changes nothing

Runs from nox-provisioner.service (deploy/cloud/systemd/) with EnvironmentFile=/etc/nox-portal/provisioner.env
(root-only). The portal container has no docker socket and no root: it only queues jobs. This script polls
the portal's internal API on 127.0.0.1 with a shared token, and for each job:

  create  -> iler-client.sh create <slug> --port <free> --coins N --public-url https://<slug>.<domain>
             [--brand nox], LLM key via the instance's set-llm-keys.sh (non-interactive), Cloudflare tunnel
             ingress rule + proxied CNAME, then reports port, admin URL and the one-time setup URL
  suspend -> iler-client.sh stop <slug>
  resume  -> iler-client.sh start <slug>
  remove  -> iler-client.sh remove <slug> --yes, then the tunnel rule and the CNAME are removed

Never logs secrets: keys, tokens and setup links are redacted from every line it prints or reports.
Python 3 standard library only; RSA unsealing uses the openssl CLI.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
CF_API = "https://api.cloudflare.com/client/v4"
TOKEN_HEADER = "X-Nox-Provisioner-Token"
# environment variables never passed to child processes (iler-client.sh, install.sh, docker)
SECRET_ENV = ("NOX_PROVISIONER_TOKEN", "NOX_DEFAULT_ANTHROPIC_KEY", "CF_API_TOKEN")

_REDACT = [
    (re.compile(r"sk-ant-[A-Za-z0-9._-]+"), "sk-ant-***"),
    (re.compile(r"(token=)[^\s&\"']+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"((?:_KEY|_TOKEN|_PASSWORD|_SECRET)=)\S+"), r"\1***"),
    (re.compile(r"\x1b\[[0-9;]*m"), ""),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACT:
        text = pattern.sub(repl, text)
    return text


def log(msg: str) -> None:
    print(redact(msg), flush=True)


class ProvisionError(Exception):
    """A job failed; the message is safe to report (already redacted)."""

    def __init__(self, message: str):
        super().__init__(redact(message)[:2000])


# ---- configuration --------------------------------------------------------------------------


@dataclass
class Config:
    portal_url: str = "http://127.0.0.1:8099"
    token: str = ""
    key_file: str = "/etc/nox-portal/provisioner-key.pem"
    default_key: str = ""
    iler_client: str = "/opt/iler/bundle/deploy/cloud/iler-client.sh"
    clients_dir: str = "/opt/iler/clients"
    domain: str = "noxsw.kz"
    port_min: int = 8110
    port_max: int = 8999
    reserved_ports: set[int] = field(default_factory=lambda: {8099, 8100})
    cf_token: str = ""
    cf_account: str = ""
    cf_tunnel: str = ""
    cf_zone: str = ""
    cf_api: str = CF_API
    max_jobs: int = 5

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        e = os.environ if env is None else env
        reserved = {int(p) for p in re.split(r"[,\s]+", e.get("NOX_RESERVED_PORTS", "8099,8100")) if p.isdigit()}
        return cls(
            portal_url=e.get("NOX_PORTAL_INTERNAL_URL", cls.portal_url).rstrip("/"),
            token=e.get("NOX_PROVISIONER_TOKEN", ""),
            key_file=e.get("NOX_PROVISIONER_KEY_FILE", cls.key_file),
            default_key=e.get("NOX_DEFAULT_ANTHROPIC_KEY", ""),
            iler_client=e.get("NOX_ILER_CLIENT", cls.iler_client),
            clients_dir=e.get("NOX_CLIENTS_DIR", cls.clients_dir).rstrip("/"),
            domain=e.get("NOX_DOMAIN", cls.domain),
            port_min=int(e.get("NOX_PORT_MIN", cls.port_min)),
            port_max=int(e.get("NOX_PORT_MAX", cls.port_max)),
            reserved_ports=reserved,
            cf_token=e.get("CF_API_TOKEN", ""),
            cf_account=e.get("CF_ACCOUNT_ID", ""),
            cf_tunnel=e.get("CF_TUNNEL_ID", ""),
            cf_zone=e.get("CF_ZONE_ID", ""),
            cf_api=e.get("CF_API_URL", CF_API).rstrip("/"),
            max_jobs=int(e.get("NOX_MAX_JOBS_PER_RUN", cls.max_jobs)),
        )

    def problems(self) -> list[str]:
        out = []
        for name, value in (("NOX_PROVISIONER_TOKEN", self.token), ("CF_API_TOKEN", self.cf_token),
                            ("CF_ACCOUNT_ID", self.cf_account), ("CF_TUNNEL_ID", self.cf_tunnel),
                            ("CF_ZONE_ID", self.cf_zone)):
            if not value:
                out.append(f"{name} is not set")
        if urllib.parse.urlsplit(self.portal_url).hostname not in ("127.0.0.1", "::1"):
            out.append("NOX_PORTAL_INTERNAL_URL must point to 127.0.0.1 (the internal API is loopback-only)")
        if not Path(self.iler_client).is_file():
            out.append(f"NOX_ILER_CLIENT not found: {self.iler_client}")
        if not (1024 < self.port_min <= self.port_max < 65536):
            out.append("NOX_PORT_MIN / NOX_PORT_MAX are not a valid range")
        return out


# ---- HTTP (stdlib) --------------------------------------------------------------------------

HttpFn = Callable[[str, str, dict, "dict | None"], "tuple[int, dict | None]"]


def http_json(method: str, url: str, headers: dict, body: dict | None = None) -> tuple[int, dict | None]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers, **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https/loopback URLs
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, OSError) as exc:
        raise ProvisionError(f"{method} {urllib.parse.urlsplit(url).netloc}: {exc}") from None
    if not raw:
        return status, None
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, {"raw": raw[:300].decode("utf-8", "replace")}


# ---- portal internal API --------------------------------------------------------------------


class Portal:
    def __init__(self, cfg: Config, http: HttpFn = http_json):
        self.cfg, self.http = cfg, http

    def _headers(self) -> dict:
        return {TOKEN_HEADER: self.cfg.token}

    def ping(self) -> None:
        status, _ = self.http("GET", f"{self.cfg.portal_url}/internal/ping", self._headers(), None)
        if status != 200:
            raise ProvisionError(f"portal internal API answered {status} (token wrong?)")

    def claim(self) -> dict | None:
        status, body = self.http("POST", f"{self.cfg.portal_url}/internal/jobs/claim", self._headers(), None)
        if status == 204:
            return None
        if status != 200 or not body:
            raise ProvisionError(f"claim failed: HTTP {status}")
        return body

    def report(self, job_id: int, result: dict) -> None:
        status, body = self.http("POST", f"{self.cfg.portal_url}/internal/jobs/{job_id}/result", self._headers(),
                                 result)
        if status != 200:
            raise ProvisionError(f"report for job {job_id} failed: HTTP {status} {json.dumps(body)[:300]}")


# ---- Cloudflare ------------------------------------------------------------------------------


def is_catch_all(rule: dict) -> bool:
    return not rule.get("hostname") and not rule.get("path")


def merge_ingress(ingress: list[dict], host: str, service: str) -> tuple[list[dict], bool]:
    """Put `host -> service` into a tunnel ingress list. Keeps every other rule in its order, replaces an
    older rule for the same host (without path), and keeps a catch-all last (adds http_status:404 if none)."""
    host = host.lower()
    rules = [dict(r) for r in ingress]
    current = [r for r in rules if (r.get("hostname") or "").lower() == host and not r.get("path")]
    if len(current) == 1 and current[0].get("service") == service and rules and is_catch_all(rules[-1]):
        return rules, False
    rules = [r for r in rules if not ((r.get("hostname") or "").lower() == host and not r.get("path"))]
    if not rules or not is_catch_all(rules[-1]):
        rules.append({"service": "http_status:404"})
    rules.insert(len(rules) - 1, {"hostname": host, "service": service})
    return rules, rules != [dict(r) for r in ingress]


def drop_ingress(ingress: list[dict], host: str) -> tuple[list[dict], bool]:
    host = host.lower()
    rules = [dict(r) for r in ingress if not ((r.get("hostname") or "").lower() == host and not r.get("path"))]
    if not rules or not is_catch_all(rules[-1]):
        rules.append({"service": "http_status:404"})
    return rules, rules != [dict(r) for r in ingress]


class Cloudflare:
    """Remotely managed tunnel (dashboard config): GET/PUT /accounts/{a}/cfd_tunnel/{t}/configurations,
    DNS via /zones/{z}/dns_records. Token scopes: Account / Cloudflare Tunnel / Edit, Zone / DNS / Edit."""

    def __init__(self, cfg: Config, http: HttpFn = http_json):
        self.cfg, self.http = cfg, http

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        status, data = self.http(method, f"{self.cfg.cf_api}{path}",
                                 {"Authorization": f"Bearer {self.cfg.cf_token}"}, body)
        if not data or not data.get("success", False) or status >= 300:
            errors = (data or {}).get("errors") or []
            detail = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errors if isinstance(e, dict))
            raise ProvisionError(f"Cloudflare {method} {path.split('?')[0]} -> HTTP {status} {detail}".strip())
        return data

    @property
    def target(self) -> str:
        return f"{self.cfg.cf_tunnel}.cfargotunnel.com"

    def _config_path(self) -> str:
        return f"/accounts/{self.cfg.cf_account}/cfd_tunnel/{self.cfg.cf_tunnel}/configurations"

    def get_config(self) -> dict:
        result = self._call("GET", self._config_path()).get("result") or {}
        return dict(result.get("config") or {})

    def put_ingress(self, config: dict, ingress: list[dict]) -> None:
        # PUT replaces the whole config: send everything we got back, with the new ingress list
        self._call("PUT", self._config_path(), {"config": {**config, "ingress": ingress}})

    def ensure_route(self, host: str, port: int) -> None:
        config = self.get_config()
        ingress, changed = merge_ingress(config.get("ingress") or [], host, f"http://127.0.0.1:{port}")
        if changed:
            self.put_ingress(config, ingress)
            log(f"tunnel: {host} -> http://127.0.0.1:{port}")
        else:
            log(f"tunnel: {host} already routed")
        self.ensure_dns(host)

    def _records(self, host: str) -> list[dict]:
        query = urllib.parse.urlencode({"name.exact": host, "per_page": 50})
        return list(self._call("GET", f"/zones/{self.cfg.cf_zone}/dns_records?{query}").get("result") or [])

    def ensure_dns(self, host: str) -> None:
        records = self._records(host)
        ours = [r for r in records if r.get("type") == "CNAME" and (r.get("content") or "").lower() == self.target]
        if ours:
            if not ours[0].get("proxied"):
                self._call("PATCH", f"/zones/{self.cfg.cf_zone}/dns_records/{ours[0]['id']}", {"proxied": True})
                log(f"dns: {host} now proxied")
            else:
                log(f"dns: {host} already points to the tunnel")
            return
        if records:
            kinds = ", ".join(sorted({str(r.get("type")) for r in records}))
            raise ProvisionError(f"DNS already has {kinds} record(s) for {host} that do not point to the tunnel; "
                                 "not touching them, fix in the Cloudflare dashboard")
        self._call("POST", f"/zones/{self.cfg.cf_zone}/dns_records",
                   {"type": "CNAME", "name": host, "content": self.target, "ttl": 1, "proxied": True,
                    "comment": "Nox bot (nox-provisioner)"})
        log(f"dns: CNAME {host} -> tunnel created")

    def remove_route(self, host: str) -> None:
        config = self.get_config()
        ingress, changed = drop_ingress(config.get("ingress") or [], host)
        if changed:
            self.put_ingress(config, ingress)
            log(f"tunnel: {host} removed")
        for r in self._records(host):
            if r.get("type") == "CNAME" and (r.get("content") or "").lower() == self.target:
                self._call("DELETE", f"/zones/{self.cfg.cf_zone}/dns_records/{r['id']}")
                log(f"dns: CNAME {host} deleted")


# ---- host helpers ---------------------------------------------------------------------------

RunFn = Callable[[list, "dict | None"], "tuple[int, str]"]


def child_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV and not k.startswith("CF_")}
    env.update(extra or {})
    return env


def run_cmd(cmd: list, extra_env: dict | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run([str(c) for c in cmd], env=child_env(extra_env), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def tail(output: str, lines: int = 12) -> str:
    kept = [ln for ln in redact(output).splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def env_get(path: Path, key: str) -> str:
    """First KEY=value line, value taken literally (same as deploy/lib/env.sh env_get)."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line[len(key) + 1:]
    except OSError:
        pass
    return ""


def port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def unseal(blob: str, key_file: str) -> str:
    """Open an RSA-OAEP-SHA256 blob sealed by the portal; the plaintext goes through a pipe, never argv."""
    import base64

    try:
        ciphertext = base64.b64decode(blob, validate=True)
    except ValueError:
        raise ProvisionError("sealed key is not valid base64") from None
    try:
        proc = subprocess.run(
            ["openssl", "pkeyutl", "-decrypt", "-inkey", key_file, "-pkeyopt", "rsa_padding_mode:oaep",
             "-pkeyopt", "rsa_oaep_md:sha256", "-pkeyopt", "rsa_mgf1_md:sha256"],
            input=ciphertext, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisionError(f"openssl failed: {type(exc).__name__}") from None
    if proc.returncode != 0:
        raise ProvisionError("could not unseal the per-client key (wrong provisioner key pair?)")
    return proc.stdout.decode("utf-8").strip()


# ---- jobs -----------------------------------------------------------------------------------


class Provisioner:
    def __init__(self, cfg: Config, portal: Portal, cf: Cloudflare, run: RunFn = run_cmd,
                 listening: Callable[[int], bool] = port_listening,
                 unseal_fn: Callable[[str], str] | None = None,
                 setup_state: Callable[[int], "bool | None"] | None = None):
        self.cfg, self.portal, self.cf, self.run, self.listening = cfg, portal, cf, run, listening
        self.unseal = unseal_fn or (lambda blob: unseal(blob, cfg.key_file))
        self.setup_state = setup_state or self._setup_state
        self._brand: bool | None = None

    # -- helpers --

    def client_dir(self, slug: str) -> Path:
        return Path(self.cfg.clients_dir) / slug

    def host(self, slug: str) -> str:
        return f"{slug}.{self.cfg.domain}"

    def supports_brand(self) -> bool:
        if self._brand is None:
            rc, out = self.run(["bash", self.cfg.iler_client, "--help"], None)
            self._brand = rc == 0 and "--brand" in out
            if not self._brand:
                log("warning: iler-client.sh has no --brand option yet; instance created with its default brand")
        return self._brand

    def used_ports(self) -> set[int]:
        used = set(self.cfg.reserved_ports)
        for env in Path(self.cfg.clients_dir).glob("*/.env"):
            value = env_get(env, "ILER_HTTP_PORT")
            if value.isdigit():
                used.add(int(value))
        return used

    def pick_port(self) -> int:
        used = self.used_ports()
        for port in range(self.cfg.port_min, self.cfg.port_max + 1):
            if port not in used and not self.listening(port):
                return port
        raise ProvisionError(f"no free port in {self.cfg.port_min}-{self.cfg.port_max}")

    def _setup_state(self, port: int) -> bool | None:
        try:
            status, body = http_json("GET", f"http://127.0.0.1:{port}/api/setup/state", {}, None)
        except ProvisionError:
            return None
        return bool(body.get("needs_setup")) if status == 200 and body else None

    def iler(self, *args, env: dict | None = None) -> str:
        rc, out = self.run(["bash", self.cfg.iler_client, *[str(a) for a in args]], env)
        if rc != 0:
            raise ProvisionError(f"iler-client.sh {args[0]} {args[1] if len(args) > 1 else ''} failed (exit {rc}):\n"
                                 f"{tail(out)}")
        return out

    # -- kinds --

    def do_create(self, job: dict) -> dict:
        slug, public_url = job["slug"], job["public_url"]
        if public_url != f"https://{self.host(slug)}":
            raise ProvisionError(f"unexpected public_url {public_url} for {slug}")
        directory = self.client_dir(slug)
        env_file = directory / ".env"
        if env_file.is_file():
            # a retry after a lost report, or after a failure past the install step: reuse the instance
            if env_get(env_file, "ILER_PROJECT") != f"iler-{slug}" or env_get(env_file, "ILER_PUBLIC_URL") != public_url:
                raise ProvisionError(f"{directory} exists with other settings; inspect it (iler-client.sh list)")
            port = int(env_get(env_file, "ILER_HTTP_PORT") or 0)
            if not port:
                raise ProvisionError(f"{env_file} has no ILER_HTTP_PORT")
            log(f"{slug}: instance exists on port {port}, continuing")
            self.iler("start", slug)
        else:
            port = self.pick_port()
            coins = job.get("coins")
            args = ["create", slug, "--port", port, "--public-url", public_url]
            if coins is not None:
                args += ["--coins", int(coins)]
            if self.supports_brand():
                args += ["--brand", "nox"]
            log(f"{slug}: creating instance on 127.0.0.1:{port}")
            try:
                self.iler(*args)
            except ProvisionError:
                if directory.exists():
                    # created by this attempt: leave nothing half-installed, so a retry starts clean
                    log(f"{slug}: create failed, removing the partial instance")
                    self.run(["bash", self.cfg.iler_client, "remove", slug, "--yes"], None)
                raise
        self.set_llm_key(job, directory)
        self.cf.ensure_route(self.host(slug), port)
        token = env_get(env_file, "ILER_SETUP_TOKEN")
        setup_url = None
        if token and self.setup_state(port):
            setup_url = f"{public_url}/setup?token={token}"
        elif token:
            log(f"{slug}: owner account already exists (or setup state unknown); no setup link reported")
        return {"ok": True, "port": port, "admin_url": public_url, "setup_url": setup_url}

    def set_llm_key(self, job: dict, directory: Path) -> None:
        sealed = job.get("llm_key_sealed")
        key = self.unseal(sealed) if sealed else self.cfg.default_key
        if not key:
            raise ProvisionError("no Anthropic key: neither a per-client key nor NOX_DEFAULT_ANTHROPIC_KEY is set")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", key):
            raise ProvisionError("the Anthropic key has characters set-llm-keys.sh does not accept")
        script = directory / "deploy" / "set-llm-keys.sh"
        # non-interactive mode of set-llm-keys.sh: values come from the environment, never from argv
        rc, out = self.run(["bash", script, "--dir", directory, "--yes"],
                           {"ILER_ANTHROPIC_KEY": key, "ILER_MONTHLY_COINS": ""})
        if rc != 0:
            raise ProvisionError(f"set-llm-keys.sh failed (exit {rc}):\n{tail(out)}")
        log(f"{job['slug']}: LLM key set ({'per-client' if sealed else 'default'})")

    def do_suspend(self, job: dict) -> dict:
        self.iler("stop", job["slug"])
        return {"ok": True}

    def do_resume(self, job: dict) -> dict:
        self.iler("start", job["slug"])
        return {"ok": True}

    def do_remove(self, job: dict) -> dict:
        slug = job["slug"]
        if (self.client_dir(slug) / ".env").is_file():
            self.iler("remove", slug, "--yes")
        else:
            log(f"{slug}: no instance on this host, removing the tunnel route only")
        self.cf.remove_route(self.host(slug))
        return {"ok": True}

    def process(self, job: dict) -> dict:
        kind, slug = job.get("kind"), str(job.get("slug", ""))
        if not SLUG_RE.match(slug):
            return {"ok": False, "error": f"invalid slug {slug!r}"}
        handler = {"create": self.do_create, "suspend": self.do_suspend, "resume": self.do_resume,
                   "remove": self.do_remove}.get(str(kind))
        if handler is None:
            return {"ok": False, "error": f"unknown job kind {kind!r}"}
        log(f"job {job['id']}: {kind} {slug}")
        try:
            result = handler(job)
        except ProvisionError as exc:
            log(f"job {job['id']}: failed: {exc}")
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - report anything, never crash the loop
            log(f"job {job['id']}: unexpected error {type(exc).__name__}: {exc}")
            return {"ok": False, "error": redact(f"internal error: {type(exc).__name__}: {exc}")}
        log(f"job {job['id']}: done")
        return result

    def run_once(self) -> int:
        processed = 0
        while processed < self.cfg.max_jobs:
            job = self.portal.claim()
            if job is None:
                break
            result = self.process(job)
            try:
                self.portal.report(job["id"], result)
            except ProvisionError as exc:
                # the portal marks the job failed after its timeout; a retry reuses the instance
                log(f"job {job['id']}: could not report the result: {exc}")
            processed += 1
        return processed


# ---- main -----------------------------------------------------------------------------------


def _lock() -> object:
    import fcntl

    handle = open("/run/nox-provisioner.lock", "w")  # noqa: SIM115 - held for the process lifetime
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another nox-provisioner run is active; exiting")
        sys.exit(0)
    return handle


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "once"
    if command in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cfg = Config.from_env()
    problems = cfg.problems()
    if problems:
        for p in problems:
            log(f"config error: {p}")
        return 2
    portal, cf = Portal(cfg), Cloudflare(cfg)
    if command == "check":
        try:
            portal.ping()
            log("portal internal API: ok")
            ingress = cf.get_config().get("ingress") or []
            log(f"cloudflare tunnel config: ok ({len(ingress)} ingress rules)")
            cf._records(f"bot.{cfg.domain}")
            log("cloudflare DNS: ok")
        except ProvisionError as exc:
            log(f"check failed: {exc}")
            return 1
        if not cfg.default_key:
            log("warning: NOX_DEFAULT_ANTHROPIC_KEY is empty; only approvals with a per-client key will work")
        if not Path(cfg.key_file).is_file():
            log(f"warning: {cfg.key_file} missing; per-client keys cannot be unsealed")
        return 0
    if command != "once":
        log(f"unknown command: {command}")
        return 2
    if os.geteuid() != 0:
        log("run as root (systemd nox-provisioner.service)")
        return 2
    lock = _lock()
    started = time.monotonic()
    try:
        count = Provisioner(cfg, portal, cf).run_once()
    except ProvisionError as exc:
        log(f"error: {exc}")
        return 1
    finally:
        del lock
    if count:
        log(f"processed {count} job(s) in {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
