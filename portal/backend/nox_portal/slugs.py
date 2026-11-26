"""Bot address slugs: <slug>.noxsw.kz. A slug is also the instance name on the host (iler-client.sh)."""

import re

from nox_portal.config import get_settings

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")

RESERVED = frozenset(
    """www mail webmail bot api admin portal ftp smtp imap pop autodiscover cpanel ns1 ns2 test status help
    support docs blog shop app dashboard static cdn dev staging demo noxsw nox iler""".split()
)


def reserved() -> frozenset[str]:
    extra = {s.strip().lower() for s in get_settings().reserved_slugs.split(",") if s.strip()}
    return RESERVED | extra


def normalize(slug: str) -> str:
    return slug.strip().lower()


def check(slug: str) -> str | None:
    """Return a Russian error message, or None when the slug is acceptable (uniqueness checked elsewhere)."""
    if not SLUG_RE.match(slug):
        return ("адрес: 3–32 символа, латинские буквы, цифры и дефис; начинается с буквы, "
                "не заканчивается дефисом")
    if "--" in slug:
        return "адрес не может содержать два дефиса подряд"
    if slug in reserved():
        return "этот адрес зарезервирован, выберите другой"
    return None
