"""Owner account handed over by the Nox portal (cloud instances).

The portal provisioner puts the applicant's e-mail, name, company and the portal password hash into the
instance .env (ILER_OWNER_EMAIL, ILER_OWNER_NAME, ILER_COMPANY, ILER_OWNER_PASSWORD_HASH). On start, while the
instance has no users, the owner is created from them: the client logs in to the panel with the same e-mail and
password as in the portal cabinet, and the setup wizard skips its "owner" step. Both services hash with
argon2-cffi's PasswordHasher (argon2id), so the portal hash verifies here as is.

Once any user exists the variables are ignored: nothing is ever overwritten. The hash is never logged, stored
anywhere but users.password_hash, or returned by the API.
"""

import logging
import re

import asyncpg

from iler import settings_store
from iler.auth.schemas import _email
from iler.config import get_settings
from iler.settings_models import BotSettings

log = logging.getLogger("iler.setup")

# settings row: {"pending": bool, "preset_owner": bool}; pending until the owner finishes the wizard
WIZARD_KEY = "setup_wizard"
# what argon2-cffi writes: $argon2id$v=19$m=65536,t=3,p=4$<salt b64>$<hash b64>
_HASH_RE = re.compile(r"^\$argon2id\$v=\d+\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]+={0,2}$")
NAME_MAX = 200


def preset_values() -> tuple[str, str, str, str] | None:
    """(email, name, company, hash) from the environment, or None when absent or unusable."""
    s = get_settings()
    raw_email, password_hash = s.owner_email.strip(), s.owner_password_hash.strip()
    if not password_hash:
        # absent, or already wiped from .env by the cloud provisioner once the owner was created: nothing to warn about
        return None
    try:
        email = _email(raw_email)
    except ValueError:
        log.warning("ILER_OWNER_EMAIL is not a valid e-mail: owner not created, the setup link is needed")
        return None
    if not _HASH_RE.match(password_hash):
        log.warning("ILER_OWNER_PASSWORD_HASH is not an argon2id hash: owner not created, the setup link is needed")
        return None
    return email, s.owner_name.strip()[:NAME_MAX], s.company.strip()[:NAME_MAX], password_hash


async def ensure_preset_owner(pool: asyncpg.Pool) -> bool:
    """Create the owner from the portal data if no user exists yet. True when it was created now."""
    values = preset_values()
    if values is None:
        return False
    email, name, company, password_hash = values
    async with pool.acquire() as conn, conn.transaction():
        # same lock as POST /api/setup/owner: exactly one owner, whichever comes first
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('iler_setup_owner'))")
        if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM users)"):
            return False
        user_id = await conn.fetchval(
            "INSERT INTO users(email, password_hash, name, role) VALUES ($1, $2, $3, 'owner') RETURNING id",
            email, password_hash, name,
        )
        await conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES ($1, $2, now())"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            WIZARD_KEY, {"pending": True, "preset_owner": True},
        )
        await conn.execute(
            "INSERT INTO audit_log(user_id, action, details) VALUES ($1, 'setup.owner_preset', $2)",
            user_id, {"email": email, "source": "portal"},
        )
    log.info("owner account created from the portal data (ILER_OWNER_EMAIL)")
    if company:
        await _prefill_company(company)
    return True


async def _prefill_company(company: str) -> None:
    """The company goes into the template field "Название" (business_name) unless the owner already typed one."""
    try:
        bot = await settings_store.get("bot", BotSettings)
        if (bot.template_values or {}).get("business_name"):
            return
        bot.template_values = {**(bot.template_values or {}), "business_name": company}
        await settings_store.put("bot", bot)
    except Exception:  # noqa: BLE001 - a convenience: the owner can always type the name
        log.exception("could not pre-fill the company name in the bot settings")


async def wizard_state(conn: asyncpg.Pool | asyncpg.Connection) -> dict:
    value = await conn.fetchval("SELECT value FROM settings WHERE key = $1", WIZARD_KEY)
    return value if isinstance(value, dict) else {}


async def finish_wizard(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "UPDATE settings SET value = jsonb_set(value, '{pending}', 'false'::jsonb), updated_at = now() WHERE key = $1",
        WIZARD_KEY,
    )
