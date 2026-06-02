"""Shared workspace path defaults for the Python runtime."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_WORKSPACE = Path("/workspace")
LEGACY_ROOT_RUNTIME_WORKSPACE = Path("/root/.efp/workspace")


def default_runtime_workspace() -> Path:
    """Return the runtime workspace used when no explicit workspace is configured."""

    return DEFAULT_RUNTIME_WORKSPACE


def resolve_runtime_workspace(config_data: Mapping[str, Any] | None = None) -> Path:
    """Resolve runtime workspace from config data with /workspace as fallback."""

    configured = None
    if isinstance(config_data, Mapping):
        workspace = config_data.get("workspace")
        if isinstance(workspace, Mapping):
            configured = workspace.get("path")
        elif isinstance(workspace, (str, Path)):
            configured = workspace
    configured_path = _coerce_workspace_path(configured)
    if configured_path is None:
        return DEFAULT_RUNTIME_WORKSPACE
    if _is_legacy_default_workspace(configured_path):
        return DEFAULT_RUNTIME_WORKSPACE
    return configured_path


def _coerce_workspace_path(value: Any) -> Path | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        return Path(value).expanduser()
    except (TypeError, ValueError):
        return None


def _is_legacy_default_workspace(path: Path) -> bool:
    candidate = _comparison_path(path)
    if candidate is None:
        return False
    legacy_aliases = {
        _comparison_path(Path.home() / ".efp" / "workspace"),
        _comparison_path(LEGACY_ROOT_RUNTIME_WORKSPACE),
    }
    legacy_aliases.discard(None)
    return candidate in legacy_aliases


def _comparison_path(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        try:
            return path.expanduser().absolute()
        except (OSError, RuntimeError, ValueError):
            return None
