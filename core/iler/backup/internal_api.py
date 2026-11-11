"""Internal backup endpoint for the updater (docker network only; nginx returns 404 for /internal/)."""

import hmac

from fastapi import APIRouter, Header

from iler.backup import run as run_mod
from iler.config import get_settings
from iler.errors import ApiError

router = APIRouter()


@router.post("/internal/backup")
async def internal_backup(authorization: str = Header(default="")):
    token = get_settings().updater_token
    expected = f"Bearer {token}"
    if not token or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise ApiError(401, "unauthorized", "invalid updater token")
    info = await run_mod.create_backup()
    run_mod.apply_retention()
    return info.model_dump(mode="json")
