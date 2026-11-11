"""End-to-end smoke test against a running stack (compose.yml + compose.test.yml).

    docker compose -p iler-smoke -f compose.yml -f compose.test.yml up -d --build
    python tests/e2e/smoke.py --web http://localhost:8080 --fake http://127.0.0.1:18080

Walks the real path: setup wizard API -> template -> WhatsApp connect (fake Evolution) ->
inbound client message -> bot reply delivered back to WhatsApp -> conversation visible in the API.
The stack must be fresh (needs_setup = true).
"""

import argparse
import sys
import time

import httpx

PHONE = "77010000000"
EMAIL = "owner@smoke.test"
PASSWORD = "smoke-test-password-1"


def step(name: str) -> None:
    print(f"• {name}", flush=True)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def wait_until(fn, timeout: float, what: str, interval: float = 1.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = fn()
            if last:
                return last
        except httpx.HTTPError as exc:
            last = exc
        time.sleep(interval)
    fail(f"timeout waiting for {what} (last: {last!r})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", default="http://localhost:8080")
    ap.add_argument("--fake", default="http://127.0.0.1:18080")
    args = ap.parse_args()

    web = httpx.Client(base_url=args.web, timeout=15)
    fake = httpx.Client(base_url=args.fake, timeout=15)

    step("stack is healthy")
    wait_until(lambda: web.get("/health").status_code == 200, 120, "core /health via web")

    step("internal paths are not exposed through the web proxy")
    r = web.post("/internal/evolution/webhook", json={})
    if r.status_code != 404:
        fail(f"/internal/ reachable through iler-web: {r.status_code}")

    step("setup wizard: create owner")
    if not web.get("/api/setup/state").json().get("needs_setup"):
        fail("stack is not fresh (needs_setup=false)")
    r = web.post("/api/setup/owner", json={"email": EMAIL, "password": PASSWORD, "name": "Smoke"})
    if r.status_code >= 300:
        fail(f"setup/owner: {r.status_code} {r.text}")
    if web.get("/api/setup/state").json().get("needs_setup"):
        fail("needs_setup still true after creating owner")

    step("login")
    r = web.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if r.status_code != 200:
        fail(f"login: {r.status_code} {r.text}")
    csrf = web.cookies.get("iler_csrf")
    if not csrf:
        fail("no iler_csrf cookie after login")
    h = {"X-ILER-CSRF": csrf}

    step("mutation without CSRF is rejected")
    if web.post("/api/templates/computer_club/apply").status_code != 403:
        fail("mutation without CSRF header was not rejected")

    step("apply computer club template, shorten buffer")
    r = web.post("/api/templates/computer_club/apply", headers=h)
    if r.status_code >= 300:
        fail(f"apply template: {r.status_code} {r.text}")
    bot = web.get("/api/settings/bot").json()
    bot["buffer_delay_seconds"] = 3
    r = web.put("/api/settings/bot", json=bot, headers=h)
    if r.status_code >= 300:
        fail(f"put bot settings: {r.status_code} {r.text}")

    step("connect WhatsApp (fake Evolution)")
    fake.post("/_test/reset")
    r = web.post("/api/whatsapp/connect", headers=h)
    if r.status_code >= 300:
        fail(f"whatsapp/connect: {r.status_code} {r.text}")
    fake.post("/_test/connection", json={"state": "open"})
    wait_until(lambda: web.get("/api/whatsapp/status").json().get("status") == "open", 30, "whatsapp status open")

    step("client writes, bot answers in WhatsApp")
    r = fake.post("/_test/inbound", json={"phone": PHONE, "text": "Привет! Есть свободные места вечером?", "push_name": "Smoke"})
    if r.status_code >= 300:
        fail(f"fake inbound: {r.status_code} {r.text}")

    def bot_replied():
        sent = fake.get("/_test/sent").json()
        items = sent if isinstance(sent, list) else sent.get("items", sent.get("sent", []))
        return [m for m in items if PHONE in str(m.get("number", m))]

    replies = wait_until(bot_replied, 40, "bot reply in fake Evolution")
    print(f"  reply: {replies[0].get('text', replies[0])!r}")

    step("conversation and messages visible in the admin API")
    convs = web.get("/api/conversations").json()["items"]
    conv = next((c for c in convs if c["phone"] == PHONE), None)
    if not conv:
        fail(f"conversation for {PHONE} not found: {convs}")
    msgs = web.get(f"/api/conversations/{conv['id']}/messages").json()["items"]
    authors = {m["author"] for m in msgs}
    if not {"client", "bot"} <= authors:
        fail(f"expected client and bot messages, got authors {authors}")

    step("dashboard counts today's activity")
    dash = web.get("/api/dashboard").json()
    if dash["today"]["client_messages"] < 1 or dash["today"]["bot_replies"] < 1:
        fail(f"dashboard counters wrong: {dash['today']}")

    print("SMOKE OK")


if __name__ == "__main__":
    main()
