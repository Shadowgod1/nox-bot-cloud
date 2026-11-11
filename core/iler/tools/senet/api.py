"""SENET integration API: settings (key masked) and connection test."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, SecretStr, field_validator

from iler import settings_store
from iler.auth.deps import csrf_protect, current_user, require_owner
from iler.settings_models import SenetSettings
from iler.tools.senet.client import SenetClient, SenetError, valid_orgname

router = APIRouter()

MASK = "••••"


def mask_key(key: SecretStr | None) -> str | None:
    if key is None or not key.get_secret_value():
        return None
    raw = key.get_secret_value()
    return MASK + raw[-4:] if len(raw) >= 8 else MASK  # short keys: do not reveal half of them


def _is_masked(value: str | None) -> bool:
    return value is not None and value.startswith(MASK)


def _normalize_orgname(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().lower()
    if not v:
        return None
    if not valid_orgname(v):
        raise ValueError("orgname must be the club subdomain: latin letters, digits and '-'")
    return v


class SenetOut(BaseModel):
    enabled: bool
    orgname: str | None
    app_key: str | None  # masked


class SenetIn(BaseModel):
    enabled: bool = False
    orgname: str | None = None
    app_key: str | None = None  # None or masked value -> keep stored key; "" -> clear

    @field_validator("orgname")
    @classmethod
    def check_orgname(cls, v: str | None) -> str | None:
        return _normalize_orgname(v)


class SenetTestIn(BaseModel):
    """Optional: test unsaved values from the form. Missing / masked fields fall back to stored ones."""

    orgname: str | None = None
    app_key: str | None = None

    @field_validator("orgname")
    @classmethod
    def check_orgname(cls, v: str | None) -> str | None:
        return _normalize_orgname(v)


def _out(s: SenetSettings) -> SenetOut:
    return SenetOut(enabled=s.enabled, orgname=s.orgname, app_key=mask_key(s.app_key))


@router.get("/api/integrations/senet", response_model=SenetOut)
async def get_senet(_user=Depends(current_user)):
    return _out(await settings_store.get("senet", SenetSettings))


@router.put("/api/integrations/senet", response_model=SenetOut, dependencies=[Depends(csrf_protect)])
async def put_senet(body: SenetIn, _user=Depends(require_owner)):
    stored = await settings_store.get("senet", SenetSettings)
    if body.app_key is None or _is_masked(body.app_key):
        key = stored.app_key
    else:
        key = SecretStr(body.app_key.strip()) if body.app_key.strip() else None
    new = SenetSettings(enabled=body.enabled, orgname=body.orgname, app_key=key)
    await settings_store.put("senet", new)
    return _out(new)


@router.post("/api/integrations/senet/test", dependencies=[Depends(csrf_protect)])
async def test_senet(body: SenetTestIn | None = None, _user=Depends(require_owner)):
    stored = await settings_store.get("senet", SenetSettings)
    orgname = (body.orgname if body and body.orgname else None) or stored.orgname
    if body and body.app_key and not _is_masked(body.app_key):
        key = body.app_key.strip()
    else:
        key = stored.app_key.get_secret_value() if stored.app_key else ""
    if not orgname or not key:
        return {"ok": False, "error": "not_configured"}
    try:
        items = await SenetClient(orgname, key).list_workstations(use_cache=False)
    except SenetError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "count": len(items)}
