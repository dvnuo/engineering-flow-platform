from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

DEFAULT_TOOLS_DIR = "/app/tools"
logger = logging.getLogger(__name__)


@dataclass
class ExternalToolsState:
    tools_dir: Path
    available: bool
    error: Optional[str] = None
    registry: Any = None
    runner: Any = None


_cached_state: Optional[ExternalToolsState] = None


def resolve_external_tools_dir() -> Path:
    return Path(os.environ.get("EFP_TOOLS_DIR", DEFAULT_TOOLS_DIR))


def external_tools_enabled() -> bool:
    return os.environ.get("EFP_EXTERNAL_TOOLS_ENABLED", "true").strip().lower() != "false"


def clear_external_tools_cache() -> None:
    global _cached_state
    _cached_state = None
    for mod_name in list(sys.modules.keys()):
        if mod_name == "efp_tools" or mod_name.startswith("efp_tools."):
            sys.modules.pop(mod_name, None)


def _strict_mode() -> bool:
    return os.environ.get("EFP_EXTERNAL_TOOLS_STRICT", "false").strip().lower() == "true"


def load_external_tools_state() -> ExternalToolsState:
    global _cached_state
    if _cached_state is not None:
        return _cached_state

    tools_dir = resolve_external_tools_dir()
    if not external_tools_enabled():
        _cached_state = ExternalToolsState(tools_dir=tools_dir, available=False, error="external tools disabled")
        return _cached_state

    python_dir = tools_dir / "python"
    if not python_dir.exists():
        _cached_state = ExternalToolsState(tools_dir=tools_dir, available=False, error=f"missing tools python dir: {python_dir}")
        return _cached_state

    try:
        importlib.invalidate_caches()
        python_dir_str = str(python_dir)
        if python_dir_str not in sys.path:
            sys.path.insert(0, python_dir_str)
        registry_mod = importlib.import_module("efp_tools.registry")
        runner_mod = importlib.import_module("efp_tools.runner")
        registry = None
        if hasattr(registry_mod, "load_registry"):
            registry = registry_mod.load_registry(str(tools_dir))
        elif hasattr(registry_mod, "ToolRegistry"):
            registry = registry_mod.ToolRegistry(str(tools_dir))
        _cached_state = ExternalToolsState(tools_dir=tools_dir, available=True, registry=registry, runner=runner_mod)
        return _cached_state
    except Exception as exc:
        if _strict_mode():
            raise
        logger.warning("Failed to load external tools registry: %s", exc)
        _cached_state = ExternalToolsState(tools_dir=tools_dir, available=False, error=str(exc))
        return _cached_state


def _descriptor_runtime_compatible(descriptor: Dict[str, Any], runtime_type: str) -> bool:
    runtime_compat = descriptor.get("runtime_compat")
    if runtime_compat is None:
        return True
    if isinstance(runtime_compat, list):
        return runtime_type in runtime_compat
    return False


def _iter_descriptors(state: ExternalToolsState) -> List[Dict[str, Any]]:
    if not state.registry:
        return []
    registry = state.registry
    if hasattr(registry, "list_descriptors"):
        return list(registry.list_descriptors() or [])
    if hasattr(registry, "descriptors"):
        descriptors = registry.descriptors
        if isinstance(descriptors, dict):
            return list(descriptors.values())
        return list(descriptors or [])
    return []


def _descriptor_name(descriptor: Dict[str, Any]) -> str:
    return str(descriptor.get("name") or "").strip()


def _descriptor_to_schema(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(descriptor.get("schema"), dict):
        return descriptor["schema"]
    name = _descriptor_name(descriptor)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": descriptor.get("description") or "",
            "parameters": descriptor.get("parameters") or {"type": "object", "properties": {}},
        },
        "metadata": dict(descriptor.get("metadata") or {}),
    }


def get_external_tool_schemas(runtime_type: str = "native") -> List[Dict[str, Any]]:
    state = load_external_tools_state()
    if not state.available:
        return []
    schemas: List[Dict[str, Any]] = []
    for descriptor in _iter_descriptors(state):
        if not isinstance(descriptor, dict):
            continue
        if not _descriptor_runtime_compatible(descriptor, runtime_type):
            continue
        if descriptor.get("enabled", True) is not True:
            continue
        metadata = descriptor.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("model_facing") is False:
            continue
        schemas.append(_descriptor_to_schema(descriptor))
    return schemas


def get_external_disabled_tool_names(runtime_type: str = "native") -> Set[str]:
    state = load_external_tools_state()
    if not state.available:
        return set()
    names: Set[str] = set()
    for descriptor in _iter_descriptors(state):
        if not isinstance(descriptor, dict):
            continue
        if not _descriptor_runtime_compatible(descriptor, runtime_type):
            continue
        if descriptor.get("enabled", True) is False:
            name = _descriptor_name(descriptor)
            if name:
                names.add(name)
    return names


def has_external_tool(name: str, runtime_type: str = "native") -> bool:
    state = load_external_tools_state()
    if not state.available:
        return False
    for descriptor in _iter_descriptors(state):
        if not isinstance(descriptor, dict):
            continue
        if _descriptor_name(descriptor) == name and _descriptor_runtime_compatible(descriptor, runtime_type):
            return True
    return False


async def execute_external_tool(
    name: str,
    kwargs: Dict[str, Any],
    *,
    session_id: Optional[str] = None,
    runtime_type: str = "native",
) -> Any | None:
    state = load_external_tools_state()
    if not state.available:
        return None

    descriptor: Optional[Dict[str, Any]] = None
    for item in _iter_descriptors(state):
        if not isinstance(item, dict):
            continue
        if _descriptor_name(item) == name and _descriptor_runtime_compatible(item, runtime_type):
            descriptor = item
            break
    if descriptor is None:
        return None
    if descriptor.get("enabled", True) is False:
        return {"success": False, "content": "", "error": f"Tool '{name}' is disabled by external descriptor"}

    context = {
        "runtime_type": runtime_type,
        "session_id": session_id or kwargs.get("_session_id"),
        "workspace_dir": os.environ.get("EFP_WORKSPACE_DIR") or str(Path.home() / ".efp" / "workspace"),
        "portal_metadata": {},
    }

    runner = state.runner
    execute_fn = getattr(runner, "execute_tool", None)
    if not callable(execute_fn):
        return {"success": False, "content": "", "error": "external tools runner missing execute_tool"}

    result = execute_fn(name, kwargs, context=context)
    if inspect.isawaitable(result):
        return await result
    return result
