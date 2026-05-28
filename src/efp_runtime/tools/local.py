"""Workspace-local Python tool loading for Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import fields
import hashlib
import importlib.util
import inspect
from pathlib import Path
from types import ModuleType
from typing import Any

from ..permissions import PermissionMetadata
from .definition import OutputPolicy, ToolContext, ToolDef
from .registry import ToolRegistry


_DEFAULT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
_SINGLE_EXPORTS = ("TOOL", "tool")
_MAPPING_EXPORTS = ("TOOLS", "tools")


def default_local_tool_directories(
    workspace_root: str | Path,
    *,
    include_defaults: bool = True,
) -> list[Path]:
    """Return existing default local Python tool directories in load order."""

    if not include_defaults:
        return []
    root = Path(workspace_root).expanduser().resolve(strict=False)
    directories: list[Path] = []
    for directory in (
        root / ".opencode" / "tool",
        root / ".opencode" / "tools",
    ):
        path = directory.resolve(strict=False)
        if path.is_dir():
            directories.append(path)
    return directories


def local_tool_defs(directories: Iterable[str | Path]) -> list[ToolDef]:
    """Load direct child ``*.py`` local tool files as Runtime v2 ``ToolDef``s."""

    definitions: list[ToolDef] = []
    for directory in directories:
        root = Path(directory).expanduser().resolve(strict=False)
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix != ".py":
                continue
            definitions.extend(_tool_defs_from_file(path.resolve(strict=False)))
    return definitions


def register_local_tools(
    registry: ToolRegistry,
    directories: Iterable[str | Path],
    *,
    allow_override: bool = False,
) -> list[str]:
    """Register workspace-local Python tools into an existing registry."""

    registered: list[str] = []
    for tool in local_tool_defs(directories):
        registry.register(tool, replace=allow_override)
        registered.append(tool.id)
    return registered


def _tool_defs_from_file(path: Path) -> list[ToolDef]:
    module = _load_module(path)
    definitions: list[ToolDef] = []
    file_stem = path.stem

    for export_name in _SINGLE_EXPORTS:
        if not hasattr(module, export_name):
            continue
        definitions.append(
            _tool_def_from_spec(
                getattr(module, export_name),
                default_id=file_stem,
                source_file=path,
                export_label=export_name,
            )
        )

    for export_name in _MAPPING_EXPORTS:
        if not hasattr(module, export_name):
            continue
        exported = getattr(module, export_name)
        if not isinstance(exported, Mapping):
            raise TypeError(f"{path}:{export_name} must be a mapping")
        for name, spec in exported.items():
            name_text = _required_text(name, f"{path}:{export_name} export name")
            definitions.append(
                _tool_def_from_spec(
                    spec,
                    default_id=f"{file_stem}_{name_text}",
                    source_file=path,
                    export_label=f"{export_name}.{name_text}",
                )
            )

    return definitions


def _load_module(path: Path) -> ModuleType:
    module_hash = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    module_name = f"_efp_runtime_local_tool_{module_hash}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load local tool file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tool_def_from_spec(
    spec: Any,
    *,
    default_id: str,
    source_file: Path,
    export_label: str,
) -> ToolDef:
    if not isinstance(spec, Mapping):
        raise TypeError(f"{source_file}:{export_label} must be a mapping")

    spec_mapping = dict(spec)
    tool_id = _optional_text(spec_mapping.get("id")) or _optional_text(
        spec_mapping.get("name")
    )
    if tool_id is None:
        tool_id = default_id
    description = _required_text(
        spec_mapping.get("description"),
        f"{source_file}:{export_label} description",
    )
    execute = spec_mapping.get("execute")
    if not callable(execute):
        raise TypeError(f"{source_file}:{export_label} execute must be callable")

    metadata = _copy_optional_mapping(
        spec_mapping.get("metadata"),
        f"{source_file}:{export_label} metadata",
    )
    metadata.update(
        {
            "local_tool": True,
            "local_tool_file": str(source_file),
            "local_tool_export": export_label,
        }
    )

    return ToolDef(
        id=_required_text(tool_id, f"{source_file}:{export_label} id"),
        description=description,
        input_schema=_input_schema(spec_mapping, source_file, export_label),
        execute=_local_execute(execute),
        permission=_permission_metadata(spec_mapping.get("permission")),
        output_policy=_output_policy(spec_mapping.get("output_policy")),
        metadata=metadata,
    )


def _input_schema(
    spec: Mapping[str, Any],
    source_file: Path,
    export_label: str,
) -> dict[str, Any]:
    for key in ("input_schema", "schema", "args_schema"):
        if key in spec:
            return _copy_mapping(
                spec[key],
                f"{source_file}:{export_label} {key}",
            )
    return deepcopy(_DEFAULT_INPUT_SCHEMA)


def _local_execute(callable_: Any):
    style = _call_style(callable_)

    async def execute(args: dict[str, Any], context: ToolContext) -> Any:
        if style == "args_context":
            result = callable_(args, context)
        elif style == "args":
            result = callable_(args)
        else:
            result = callable_()
        if inspect.isawaitable(result):
            return await result
        return result

    return execute


def _call_style(callable_: Any) -> str:
    try:
        signature = inspect.signature(callable_)
    except (TypeError, ValueError):
        return "args_context"
    for style, values in (
        ("args_context", ({}, ToolContext())),
        ("args", ({},)),
        ("none", ()),
    ):
        try:
            signature.bind(*values)
        except TypeError:
            continue
        return style
    raise TypeError(
        "Local tool execute callable must accept execute(args, context), "
        "execute(args), or execute()"
    )


def _permission_metadata(value: Any) -> PermissionMetadata:
    if value is None:
        return PermissionMetadata()
    if isinstance(value, PermissionMetadata):
        return PermissionMetadata(
            action=value.action,
            reason=value.reason,
            category=value.category,
            resource=value.resource,
            risk=value.risk,
            data=_copy_mapping(value.data, "permission data"),
        )
    if not isinstance(value, Mapping):
        raise TypeError("permission must be PermissionMetadata or a mapping")

    allowed_fields = {"action", "category", "resource", "risk", "reason", "data"}
    payload = {
        key: deepcopy(item)
        for key, item in value.items()
        if key in allowed_fields
    }
    unknown = sorted(
        str(key) for key in value if key not in {*allowed_fields, "patterns"}
    )
    if unknown:
        raise TypeError(f"permission has unsupported field(s): {', '.join(unknown)}")
    data = _copy_optional_mapping(payload.pop("data", None), "permission data")
    if "patterns" in value:
        patterns = value["patterns"]
        if isinstance(patterns, (str, bytes)):
            data["patterns"] = [
                patterns.decode("utf-8", errors="replace")
                if isinstance(patterns, bytes)
                else patterns
            ]
        else:
            data["patterns"] = [str(pattern) for pattern in patterns or []]
    return PermissionMetadata(**payload, data=data)


def _output_policy(value: Any) -> OutputPolicy:
    if value is None:
        return OutputPolicy()
    if isinstance(value, OutputPolicy):
        return OutputPolicy(
            max_chars=value.max_chars,
            max_lines=value.max_lines,
            max_bytes=value.max_bytes,
            truncation_direction=value.truncation_direction,
            archive_full_output=value.archive_full_output,
            truncate=value.truncate,
            include_raw_output=value.include_raw_output,
        )
    if not isinstance(value, Mapping):
        raise TypeError("output_policy must be OutputPolicy or a mapping")
    allowed_fields = {field.name for field in fields(OutputPolicy)}
    unknown = sorted(str(key) for key in value if key not in allowed_fields)
    if unknown:
        raise TypeError(f"output_policy has unsupported field(s): {', '.join(unknown)}")
    return OutputPolicy(**{key: deepcopy(item) for key, item in value.items()})


def _copy_optional_mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    return _copy_mapping(value, label)


def _copy_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    copied = deepcopy(dict(value))
    if isinstance(copied, dict):
        return copied
    return dict(value)


def _required_text(value: Any, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{label} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "default_local_tool_directories",
    "local_tool_defs",
    "register_local_tools",
]
