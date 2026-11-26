"""Evolution webhook: POST /internal/evolution/webhook.

Reachable only inside the docker network (nginx does not proxy /internal/), authenticated with the
`apikey` header, never rate limited (spec 11.5). MESSAGES_UPSERT is stored in `inbound_events`
(dedup by Evolution message id) and answered 200 right away; processing runs in the background.
MESSAGES_SET / CHATS_SET (history sync after linking) only feed the known-contacts list.
"""

import hmac
import json
import logging

from fastapi import APIRouter, Request

from iler import inbound
from iler.config import get_settings
from iler.errors import ApiError
from iler.whatsapp import monitor
from iler.whatsapp.parse import event_name, history_contacts, message_payloads

log = logging.getLogger("iler.whatsapp.webhook")

router = APIRouter()


def _authorized(request: Request) -> bool:
    expected = get_settings().evo_api_key
    got = request.headers.get("apikey", "")
    return bool(expected) and hmac.compare_digest(got.encode(), expected.encode())


@router.post("/internal/evolution/webhook", include_in_schema=False)
async def evolution_webhook(request: Request):
    if not _authorized(request):
        raise ApiError(401, "unauthorized", "Bad apikey")
    try:
        payload = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return {"ok": False}  # 200: a retried garbage payload would never succeed anyway
    if not isinstance(payload, dict):
        return {"ok": False}

    settings = get_settings()
    instance = payload.get("instance")
    if isinstance(instance, dict):  # defensive: older payload shapes
        instance = instance.get("instanceName")
    if instance and instance != settings.evo_instance:
        return {"ok": True, "ignored": True}

    name = event_name(payload)
    if name == "MESSAGES_UPSERT":
        stored = 0
        for single in message_payloads(payload):
            evo_id = (single["data"].get("key") or {}).get("id")
            if not evo_id:
                continue
            row = await inbound.store_event(str(evo_id), single)
            if row is not None:
                stored += 1
                inbound.schedule(row)
        return {"ok": True, "stored": stored}

    if name == "CONNECTION_UPDATE":
        data = payload.get("data") or {}
        if isinstance(data, dict) and data.get("state"):
            await monitor.apply_connection_update(
                str(data["state"]), wuid=data.get("wuid"), status_reason=data.get("statusReason")
            )
        return {"ok": True}

    if name in ("MESSAGES_SET", "CHATS_SET"):
        # history sync of a freshly linked device: remember who already wrote to this number
        from iler import reply_rules

        try:
            known = await reply_rules.remember(history_contacts(payload), "history")
        except Exception:  # noqa: BLE001 - never make Evolution retry a huge history payload
            log.exception("could not store history contacts")
            known = 0
        return {"ok": True, "known": known}

    return {"ok": True, "ignored": True}
