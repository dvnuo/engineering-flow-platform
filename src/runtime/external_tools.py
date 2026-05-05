from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Set

from src.tools_external import get_external_tool_registry, reset_external_tool_registry_cache
from src.tools_external.manifest_loader import resolve_external_tools_dir as _resolve_tools_dir
from src.tools_external.schemas import descriptor_to_tool_schema

logger = logging.getLogger(__name__)


@dataclass
class ExternalToolsState:
    tools_dir: Path
    available: bool
    error: Optional[str] = None
    registry: Any = None
    runner: Any = None
    validation_errors: list[str] = field(default_factory=list)


_cached_state: Optional[ExternalToolsState] = None


def resolve_external_tools_dir() -> Path:
    return _resolve_tools_dir()


def external_tools_enabled() -> bool:
    return os.environ.get("EFP_EXTERNAL_TOOLS_ENABLED", "true").strip().lower() != "false"


def clear_external_tools_cache() -> None:
    global _cached_state
    _cached_state = None
    reset_external_tool_registry_cache()
    for mod_name in list(sys.modules.keys()):
        if mod_name == "efp_tools" or mod_name.startswith("efp_tools."):
            sys.modules.pop(mod_name, None)


def load_external_tools_state() -> ExternalToolsState:
    global _cached_state
    if _cached_state is not None:
        return _cached_state
    tools_dir = resolve_external_tools_dir()
    if not external_tools_enabled():
        _cached_state = ExternalToolsState(tools_dir=tools_dir, available=False, error="external tools disabled")
        return _cached_state
    try:
        registry = get_external_tool_registry()
        _cached_state = ExternalToolsState(tools_dir=tools_dir, available=True, registry=registry, validation_errors=[])
    except Exception as exc:
        logger.warning("Failed to load external tools registry: %s", exc)
        _cached_state = ExternalToolsState(tools_dir=tools_dir, available=False, error=str(exc))
    return _cached_state


def get_external_tool_schemas(runtime_type: str = "native") -> list[dict[str, Any]]:
    state = load_external_tools_state()
    if not state.available or not state.registry:
        return []
    if runtime_type == "native":
        return state.registry.get_tools_schema()
    return [
        descriptor_to_tool_schema(item)
        for item in state.registry.list_descriptors(
            runtime_type=runtime_type,
            enabled_only=True,
            model_facing_only=True,
        )
    ]


def get_external_disabled_tool_names(runtime_type: str = "native") -> Set[str]:
    state = load_external_tools_state()
    if not state.registry:
        return set()
    names: Set[str] = set()
    for d in state.registry.list_all_descriptors():
        if runtime_type in (d.runtime_compat or []) and not d.enabled:
            names.add(d.name)
    return names


def has_external_tool(name: str, runtime_type: str = "native", include_disabled: bool = True) -> bool:
    state = load_external_tools_state()
    if not state.registry:
        return False
    d = state.registry.get_descriptor(name)
    if d is None:
        return False
    if runtime_type not in (d.runtime_compat or []):
        return False
    if not include_disabled and not d.enabled:
        return False
    return True


async def execute_external_tool(name: str, kwargs: dict[str, Any], *, session_id: Optional[str] = None, runtime_type: str = "native") -> Any | None:
    state = load_external_tools_state()
    if not state.registry:
        return None
    d = state.registry.get_descriptor(name)
    if d is None:
        return None
    if not d.enabled:
        return {"success": False, "content": "", "error": f"Tool '{name}' is disabled by external descriptor"}
    if runtime_type not in (d.runtime_compat or []):
        return {"success": False, "content": "", "error": f"Tool '{name}' is runtime_incompatible for '{runtime_type}'"}

    forwarded = dict(kwargs or {})
    forwarded.setdefault("_session_id", session_id)
    forwarded.setdefault("_runtime_type", runtime_type)
    result = await state.registry.execute_tool(name, **forwarded)
    return {"success": result.success, "content": result.content, "error": result.error}
