"""Bot templates: YAML files in `TEMPLATES_DIR` (default core/iler/templates/), one per template.

File body is a BotSettings document. Optional display metadata lives under a top-level `meta` key:

    meta:
      name: Компьютерный клуб
      description: ...
    name: ...            # BotSettings fields
"""

import logging
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from iler.settings_models import BotSettings

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class TemplateMeta(BaseModel):
    id: str
    name: str
    description: str = ""


class TemplateNotFound(LookupError):
    pass


class TemplateInvalid(ValueError):
    pass


def _dir(directory: Path | None) -> Path:
    return Path(directory) if directory is not None else Path(TEMPLATES_DIR)


def _read(path: Path) -> tuple[dict, BotSettings]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TemplateInvalid(f"{path.name}: {exc}") from exc
    if not isinstance(doc, dict):
        raise TemplateInvalid(f"{path.name}: top level must be a mapping")
    if "settings" in doc:
        # Shipped format: top-level id/name/description, BotSettings under `settings`.
        meta = {"name": doc.get("name"), "description": doc.get("description")}
        doc = doc["settings"]
        if not isinstance(doc, dict):
            raise TemplateInvalid(f"{path.name}: settings must be a mapping")
    else:
        meta = doc.pop("meta", None) or {}
    if not isinstance(meta, dict):
        raise TemplateInvalid(f"{path.name}: meta must be a mapping")
    try:
        return meta, BotSettings.model_validate(doc)
    except ValidationError as exc:
        raise TemplateInvalid(f"{path.name}: {exc}") from exc


def list_templates(directory: Path | None = None) -> list[TemplateMeta]:
    base = _dir(directory)
    result: list[TemplateMeta] = []
    if not base.is_dir():
        return result
    for path in sorted(base.glob("*.yaml")):
        if not _ID_RE.match(path.stem):
            continue
        try:
            meta, _ = _read(path)
        except TemplateInvalid:
            log.warning("skipping invalid bot template %s", path.name, exc_info=True)
            continue
        result.append(
            TemplateMeta(id=path.stem, name=str(meta.get("name") or path.stem), description=str(meta.get("description") or ""))
        )
    return result


def load(template_id: str, directory: Path | None = None) -> BotSettings:
    if not _ID_RE.match(template_id):
        raise TemplateNotFound(template_id)
    path = _dir(directory) / f"{template_id}.yaml"
    if not path.is_file():
        raise TemplateNotFound(template_id)
    return _read(path)[1]
