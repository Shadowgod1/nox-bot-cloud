"""Minimal stand-in for Evolution API v2, used only by the e2e smoke test (compose.test.yml).

Implements the Evolution endpoints iler-core uses plus test hooks:
  POST /_test/inbound {phone, text[, push_name]}  -> sends a MESSAGES_UPSERT webhook to the registered URL
  POST /_test/connection {state}                  -> changes connectionState and sends CONNECTION_UPDATE
  GET  /_test/sent                                -> messages sent through /message/sendText
  GET  /_test/state                               -> registered instances/webhooks, presence calls
  POST /_test/reset                               -> clears everything
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

API_KEY = os.environ.get("AUTHENTICATION_API_KEY", "")
DEFAULT_WEBHOOK_URL = os.environ.get("FAKE_DEFAULT_WEBHOOK_URL", "")
SERVER_URL = os.environ.get("FAKE_SERVER_URL", "http://fake-evolution:8080")
# 1x1 transparent PNG
FAKE_QR = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

app = FastAPI(title="fake-evolution")

STATE: dict[str, Any] = {}


def _reset() -> None:
    STATE.clear()
    STATE.update(instances={}, webhooks={}, sent=[], presence=[], webhook_deliveries=[])


_reset()


def _check_key(request: Request) -> None:
    if API_KEY and request.headers.get("apikey") != API_KEY:
        raise HTTPException(status_code=401, detail={"status": 401, "error": "Unauthorized"})


def _instance(name: str) -> dict:
    inst = STATE["instances"].get(name)
    if inst is None:
        inst = STATE["instances"][name] = {"instanceName": name, "state": "close", "owner": None}
    return inst


def _digits(value: str) -> str:
    return "".join(ch for ch in value.split("@", 1)[0] if ch.isdigit())


async def _deliver(instance: str, event: str, data: dict) -> dict:
    url = STATE["webhooks"].get(instance, {}).get("url") or DEFAULT_WEBHOOK_URL
    if not url:
        raise HTTPException(status_code=409, detail="no webhook registered (call /webhook/set first)")
    payload = {
        "event": event,
        "instance": instance,  # v2: a plain string
        "data": data,
        "destination": url,
        "date_time": datetime.now(timezone.utc).isoformat(),
        "sender": STATE["instances"].get(instance, {}).get("owner") or "",
        "server_url": SERVER_URL,
        "apikey": API_KEY,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload, headers={"apikey": API_KEY})
    record = {"url": url, "event": event, "status": resp.status_code}
    STATE["webhook_deliveries"].append(record)
    return record


@app.get("/")
async def root() -> dict:
    return {"status": 200, "message": "fake-evolution", "version": "2.3.7-fake"}


# ---- instance -----------------------------------------------------------------------------

@app.post("/instance/create")
async def instance_create(request: Request) -> dict:
    _check_key(request)
    body = await request.json()
    name = body.get("instanceName") or "iler"
    if name in STATE["instances"]:
        raise HTTPException(status_code=403, detail={"status": 403, "error": "Forbidden",
                                                     "response": {"message": [f'This name "{name}" is already in use.']}})
    inst = _instance(name)
    webhook = body.get("webhook")
    if isinstance(webhook, dict) and webhook.get("url"):
        STATE["webhooks"][name] = {"url": webhook["url"], "events": webhook.get("events", [])}
    return {
        "instance": {"instanceName": name, "instanceId": str(uuid.uuid4()), "integration": "WHATSAPP-BAILEYS",
                     "status": inst["state"]},
        "hash": uuid.uuid4().hex,
        "qrcode": {"base64": FAKE_QR, "code": "fake-qr-code"} if body.get("qrcode") else {},
    }


@app.get("/instance/fetchInstances")
async def fetch_instances(request: Request) -> list:
    _check_key(request)
    return [{"name": i["instanceName"], "connectionStatus": i["state"], "ownerJid": i["owner"]}
            for i in STATE["instances"].values()]


@app.get("/instance/connect/{name}")
async def instance_connect(name: str, request: Request) -> dict:
    _check_key(request)
    inst = _instance(name)
    number = request.query_params.get("number")
    if inst["state"] == "close":
        inst["state"] = "connecting"
    result: dict = {"base64": FAKE_QR, "code": "fake-qr-code", "count": 1}
    if number:
        result["pairingCode"] = "ABCD1234"
    return result


@app.get("/instance/connectionState/{name}")
async def connection_state(name: str, request: Request) -> dict:
    _check_key(request)
    inst = _instance(name)
    return {"instance": {"instanceName": name, "state": inst["state"]}}


@app.api_route("/instance/logout/{name}", methods=["DELETE", "POST"])
async def instance_logout(name: str, request: Request) -> dict:
    _check_key(request)
    inst = _instance(name)
    inst["state"] = "close"
    inst["owner"] = None
    return {"status": "SUCCESS", "error": False, "response": {"message": "Instance logged out"}}


# ---- webhook / messages -------------------------------------------------------------------

@app.post("/webhook/set/{name}")
async def webhook_set(name: str, request: Request) -> dict:
    _check_key(request)
    body = await request.json()
    cfg = body.get("webhook", body)  # v2.2+ nests under "webhook"; accept the flat form too
    STATE["webhooks"][name] = {"url": cfg.get("url"), "events": cfg.get("events", []),
                               "enabled": cfg.get("enabled", True)}
    _instance(name)
    return {"webhook": {"instanceName": name, **STATE["webhooks"][name]}}


@app.get("/webhook/find/{name}")
async def webhook_find(name: str, request: Request) -> dict:
    _check_key(request)
    return STATE["webhooks"].get(name, {})


@app.post("/message/sendText/{name}")
async def send_text(name: str, request: Request) -> dict:
    _check_key(request)
    body = await request.json()
    number, text = body.get("number"), body.get("text")
    if not number or text is None:
        raise HTTPException(status_code=400, detail={"status": 400, "error": "Bad Request"})
    msg_id = "FAKE" + uuid.uuid4().hex[:16].upper()
    jid = number if "@" in str(number) else f"{_digits(str(number))}@s.whatsapp.net"
    STATE["sent"].append({"instance": name, "number": str(number), "jid": jid, "text": text, "id": msg_id,
                          "ts": time.time()})
    return {
        "key": {"remoteJid": jid, "fromMe": True, "id": msg_id},
        "message": {"conversation": text},
        "messageTimestamp": int(time.time()),
        "status": "PENDING",
    }


@app.post("/chat/sendPresence/{name}")
async def send_presence(name: str, request: Request) -> dict:
    _check_key(request)
    body = await request.json()
    STATE["presence"].append({"instance": name, **body})
    return {"presence": body.get("presence")}


# ---- test hooks ---------------------------------------------------------------------------

@app.post("/_test/inbound")
async def test_inbound(request: Request) -> dict:
    body = await request.json()
    phone = _digits(str(body.get("phone", "")))
    if not phone:
        raise HTTPException(status_code=400, detail="phone required")
    instance = body.get("instance") or next(iter(STATE["instances"]), "iler")
    msg_id = body.get("id") or "FAKEIN" + uuid.uuid4().hex[:14].upper()
    data = {
        "key": {"remoteJid": f"{phone}@s.whatsapp.net", "fromMe": bool(body.get("from_me", False)), "id": msg_id},
        "pushName": body.get("push_name", "Test Client"),
        "status": "DELIVERY_ACK",
        "message": {"conversation": body.get("text", "")},
        "messageType": "conversation",
        "messageTimestamp": int(time.time()),
        "instanceId": instance,
        "source": "android",
    }
    record = await _deliver(instance, "messages.upsert", data)
    return {"id": msg_id, **record}


@app.post("/_test/connection")
async def test_connection(request: Request) -> dict:
    body = await request.json()
    instance = body.get("instance") or next(iter(STATE["instances"]), "iler")
    inst = _instance(instance)
    inst["state"] = body.get("state", "open")
    if inst["state"] == "open":
        inst["owner"] = f"{_digits(body.get('phone', '77000000000'))}@s.whatsapp.net"
    data = {"instance": instance, "state": inst["state"], "statusReason": 200,
            "wuid": inst["owner"]}
    return await _deliver(instance, "connection.update", data)


@app.get("/_test/sent")
async def test_sent() -> list:
    return STATE["sent"]


@app.get("/_test/state")
async def test_state() -> dict:
    return {k: STATE[k] for k in ("instances", "webhooks", "presence", "webhook_deliveries")}


@app.post("/_test/reset")
async def test_reset() -> dict:
    _reset()
    return {"ok": True}
