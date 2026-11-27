"""Global tool registry. Plugins call register() at import time."""

import importlib
import pkgutil

from iler.settings_models import AllSettings
from iler.tools.base import Tool

_tools: dict[str, Tool] = {}
_loaded = False


def register(tool: Tool) -> Tool:
    if tool.name in _tools and _tools[tool.name] is not tool:
        raise ValueError(f"tool already registered: {tool.name}")
    _tools[tool.name] = tool
    return tool


def load_plugins() -> None:
    global _loaded
    if _loaded:
        return
    import iler.tools.builtin as builtin

    for mod in pkgutil.iter_modules(builtin.__path__):
        importlib.import_module(f"{builtin.__name__}.{mod.name}")
    _loaded = True


def get(name: str) -> Tool:
    load_plugins()
    return _tools[name]


def all_tools() -> list[Tool]:
    load_plugins()
    return list(_tools.values())


def enabled_tools(settings: AllSettings) -> list[Tool]:
    return [t for t in all_tools() if t.enabled(settings)]
