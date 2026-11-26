"""Bot templates: YAML files in `TEMPLATES_DIR` (default core/iler/templates/), one per template.

A template is a business-type prompt with a form of fields the client fills in:

    id: beauty_salon
    name: Салон красоты / барбершоп
    description: ...
    fields:                       # rendered as a form in the UI, in this order
      - {key: business_name, label: Название, type: text, required: true, placeholder: "..."}
      - {key: faq, label: Частые вопросы, type: textarea, default: "В: ...\nО: ..."}  # default = prefilled
    defaults: {services: [{name: "Маникюр", price: "от … ₸", duration_min: 60}]}  # BotSettings presets
    prompt: |
      Ты — администратор «{{business_name}}». {{#phone}}Телефон: {{phone}}.{{/phone}}
    defaults: {name: "Администратор", greeting: "..."}   # optional BotSettings defaults

The prompt is rendered by a tiny logic-less renderer (no Jinja, no eval):
- `{{key}}` — the field value (unknown / empty -> "");
- `{{#key}}...{{/key}}` — rendered only when the field is non-empty;
- `{{^key}}...{{/key}}` — rendered only when the field is empty.
Values are substituted in one pass after sections are resolved, so a value can never inject markup.
"""

import logging
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from iler.settings_models import TEMPLATE_VALUE_MAX, BotSettings

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

# BotSettings fields a template may preset; the prompt fields are owned by the render flow.
_DEFAULTS_ALLOWED = set(BotSettings.model_fields) - {"prompt", "prompt_edited", "template_id", "template_values"}


class TemplateField(BaseModel):
    key: str
    label: str
    type: Literal["text", "textarea", "phone"] = "text"
    required: bool = False
    placeholder: str = ""
    hint: str = ""
    # onboarding/knowledge: prefilled text the owner edits (typical FAQ, tone, booking rules); "" = empty field
    default: str = Field("", max_length=TEMPLATE_VALUE_MAX)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError(f"invalid field key: {v!r}")
        return v


class TemplateMeta(BaseModel):
    """Public shape of GET /api/templates."""

    id: str
    name: str
    description: str = ""
    fields: list[TemplateField] = []
    defaults: dict = {}


class Template(TemplateMeta):
    prompt: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "Template":
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate field keys")
        unknown = set(self.defaults) - _DEFAULTS_ALLOWED
        if unknown:
            raise ValueError(f"defaults: unknown BotSettings fields {sorted(unknown)}")
        BotSettings.model_validate(self.defaults)  # defaults must be valid settings values
        return self

    def meta(self) -> TemplateMeta:
        return TemplateMeta(**self.model_dump(include={"id", "name", "description", "fields", "defaults"}))


class TemplateNotFound(LookupError):
    pass


class TemplateInvalid(ValueError):
    pass


class ValuesInvalid(ValueError):
    pass


# --- renderer ------------------------------------------------------------------------------------

_SECTION = re.compile(r"\{\{([#^])\s*([a-z][a-z0-9_]*)\s*\}\}(.*?)\{\{/\s*\2\s*\}\}", re.S)
_VAR = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")  # every control char except "\n"


def render(prompt: str, values: dict[str, str]) -> str:
    def filled(key: str) -> bool:
        return bool((values.get(key) or "").strip())

    def section(m: re.Match) -> str:
        kind, key, body = m.group(1), m.group(2), m.group(3)
        return body if filled(key) == (kind == "#") else ""

    text = prompt
    for _ in range(10):  # nested sections resolve inside-out / outside-in over a few passes
        new = _SECTION.sub(section, text)
        if new == text:
            break
        text = new
    text = _VAR.sub(lambda m: (values.get(m.group(1)) or "").strip(), text)
    # Tidy what removed sections leave behind: trailing spaces and runs of blank lines.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_values(template: Template, values: dict) -> dict[str, str]:
    """Only declared keys, strings, each <= TEMPLATE_VALUE_MAX chars; control chars (except newlines) removed."""
    if not isinstance(values, dict):
        raise ValuesInvalid("values must be an object")
    declared = {f.key for f in template.fields}
    out: dict[str, str] = {}
    for key, value in values.items():
        if key not in declared:
            raise ValuesInvalid(f"unknown field: {str(key)[:64]}")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValuesInvalid(f"field {key}: must be a string")
        value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
        value = _CONTROL.sub("", value)
        if len(value) > TEMPLATE_VALUE_MAX:
            raise ValuesInvalid(f"field {key}: longer than {TEMPLATE_VALUE_MAX} characters")
        out[key] = value
    return out


def missing_required(template: Template, values: dict[str, str]) -> list[str]:
    return [f.key for f in template.fields if f.required and not (values.get(f.key) or "").strip()]


# --- loading -------------------------------------------------------------------------------------

def _dir(directory: Path | None) -> Path:
    return Path(directory) if directory is not None else Path(TEMPLATES_DIR)


def _read(path: Path) -> Template:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TemplateInvalid(f"{path.name}: {exc}") from exc
    if not isinstance(doc, dict):
        raise TemplateInvalid(f"{path.name}: top level must be a mapping")
    doc = {**doc, "id": path.stem}  # the file name is the id (and is what the API validates)
    try:
        return Template.model_validate(doc)
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
            result.append(_read(path).meta())
        except TemplateInvalid:
            log.warning("skipping invalid bot template %s", path.name, exc_info=True)
    # `generic` is the fallback choice: keep it last.
    result.sort(key=lambda t: t.id == "generic")
    return result


def load(template_id: str, directory: Path | None = None) -> Template:
    if not _ID_RE.match(template_id):
        raise TemplateNotFound(template_id)
    base = _dir(directory).resolve()
    path = (base / f"{template_id}.yaml").resolve()
    if path.parent != base or not path.is_file():
        raise TemplateNotFound(template_id)
    return _read(path)
