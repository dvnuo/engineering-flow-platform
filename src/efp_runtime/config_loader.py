"""Workspace configuration loading for Runtime v2.

The loader is intentionally side-effect free: it reads local JSON/JSONC files
and returns Runtime v2 config objects without starting tools, MCP servers, or
provider integrations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from .agents.profile import AgentProfile
from .agents.registry import AgentRegistry
from .commands import (
    CommandDefinition,
    CommandRegistry,
    command_definitions_from_config,
)
from .runtime.config import RuntimeConfig


DEFAULT_CONFIG_FILE_NAMES = (
    "opencode.json",
    "opencode.jsonc",
    ".opencode.json",
    ".opencode/config.json",
    ".opencode/config.jsonc",
)

_RUNTIME_CONFIG_KEYS = {
    "permission",
    "permissions",
    "disabledTools",
    "disabled_tools",
    "enabledTools",
    "enabled_tools",
    "instructions",
    "systemPrompt",
    "system_prompt",
    "skillDirectories",
    "skill_directories",
    "activeSkills",
    "active_skills",
    "command",
    "commands",
    "commandDirectories",
    "command_directories",
    "runtime",
    "runtime_mode",
    "agents",
    "defaultAgent",
    "default_agent",
}

_AGENT_KEYS = {
    "name",
    "description",
    "prompt",
    "tools",
    "maxIterations",
    "max_iterations",
    "skills",
    "active_skills",
    "metadata",
}


@dataclass
class RuntimeConfigLoadResult:
    """Result returned by :func:`load_runtime_config`."""

    config: RuntimeConfig
    agent_registry: AgentRegistry | None = None
    command_registry: CommandRegistry | None = None
    command_definitions: list[CommandDefinition] = field(default_factory=list)
    loaded_paths: list[Path] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def find_runtime_config_files(
    workspace_root: str | Path,
    *,
    include_defaults: bool = True,
) -> list[Path]:
    """Return existing default Runtime v2 config files in load order."""

    if not include_defaults:
        return []
    root = _workspace_root_path(workspace_root)
    paths: list[Path] = []
    for name in DEFAULT_CONFIG_FILE_NAMES:
        path = _resolve_workspace_path(root, name)
        if path.is_file():
            paths.append(path)
    return paths


def load_runtime_config(
    workspace_root: str | Path,
    *,
    paths: Any = None,
    include_defaults: bool = True,
) -> RuntimeConfigLoadResult:
    """Load Runtime v2 config files from a workspace.

    Multiple files are merged in ``loaded_paths`` order. Later files override
    earlier scalar values, mappings are deep-merged, and lists are appended with
    stable de-duplication.
    """

    root = _workspace_root_path(workspace_root)
    candidates = _candidate_config_paths(
        root,
        paths=paths,
        include_defaults=include_defaults,
    )
    loaded_paths: list[Path] = []
    raw: dict[str, Any] = {}

    for path in candidates:
        if not path.is_file():
            continue
        payload = _read_config_file(path)
        raw = _deep_merge(raw, payload)
        loaded_paths.append(path)

    metadata = _loader_metadata(raw, loaded_paths)
    config = _runtime_config_from_raw(raw, root, metadata)
    agent_registry = _agent_registry_from_raw(raw)
    command_definitions = command_definitions_from_config(raw)
    command_registry = _command_registry_from_sources(
        definitions=command_definitions,
        command_directories=config.command_directories,
    )

    return RuntimeConfigLoadResult(
        config=config,
        agent_registry=agent_registry,
        command_registry=command_registry,
        command_definitions=command_definitions,
        loaded_paths=loaded_paths,
        raw=raw,
        metadata=metadata,
    )


def _candidate_config_paths(
    workspace_root: Path,
    *,
    paths: Any,
    include_defaults: bool,
) -> list[Path]:
    candidates: list[Path] = []
    if include_defaults:
        candidates.extend(find_runtime_config_files(workspace_root, include_defaults=True))
    if paths is not None:
        candidates.extend(_resolve_config_paths(workspace_root, paths))
    return _dedupe_paths(candidates)


def _resolve_config_paths(workspace_root: Path, paths: Any) -> list[Path]:
    return [
        _resolve_workspace_path(workspace_root, path)
        for path in _coerce_sequence(paths)
        if _path_has_value(path)
    ]


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".jsonc":
            text = _remove_trailing_commas(_strip_jsonc_comments(text))
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path}: {exc.msg} "
            f"at line {exc.lineno} column {exc.colno}"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise ValueError(f"Runtime config file must contain a JSON object: {path}")
    return dict(loaded)


def _strip_jsonc_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    length = len(text)

    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""

        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            output.extend((" ", " "))
            index += 2
            while index < length and text[index] not in "\r\n":
                output.append(" ")
                index += 1
            continue

        if char == "/" and next_char == "*":
            output.extend((" ", " "))
            index += 2
            while index < length:
                if text[index] == "*" and index + 1 < length and text[index + 1] == "/":
                    output.extend((" ", " "))
                    index += 2
                    break
                output.append("\n" if text[index] == "\n" else " ")
                index += 1
            continue

        output.append(char)
        index += 1

    return "".join(output)


def _remove_trailing_commas(text: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    length = len(text)

    while index < length:
        char = text[index]

        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < length and text[lookahead].isspace():
                lookahead += 1
            if lookahead < length and text[lookahead] in "]}":
                index += 1
                continue

        output.append(char)
        index += 1

    return "".join(output)


def _runtime_config_from_raw(
    raw: Mapping[str, Any],
    workspace_root: Path,
    metadata: Mapping[str, Any],
) -> RuntimeConfig:
    kwargs: dict[str, Any] = {
        "workspace_root": workspace_root,
        "metadata": dict(metadata),
    }

    permissions = _merged_alias_mapping(raw, ("permission", "permissions"))
    if permissions is not None:
        kwargs["tool_permissions"] = permissions

    enabled_tools = _merged_alias_strings(raw, ("enabledTools", "enabled_tools"))
    if enabled_tools is not None:
        kwargs["enabled_tools"] = enabled_tools

    disabled_tools = _merged_alias_strings(raw, ("disabledTools", "disabled_tools"))
    if disabled_tools is not None:
        kwargs["disabled_tools"] = disabled_tools

    instruction_paths, instruction_texts = _instruction_sources(
        raw.get("instructions"),
        workspace_root=workspace_root,
    )
    if instruction_paths:
        kwargs["instruction_paths"] = instruction_paths
    if instruction_texts:
        kwargs["instruction_texts"] = instruction_texts

    system_prompt_texts = _merged_alias_strings(raw, ("systemPrompt", "system_prompt"))
    if system_prompt_texts is not None:
        kwargs["system_prompt_texts"] = system_prompt_texts

    skill_directories = _merged_alias_paths(
        raw,
        ("skillDirectories", "skill_directories"),
        workspace_root=workspace_root,
    )
    if skill_directories is not None:
        kwargs["skill_directories"] = skill_directories

    active_skills = _merged_alias_strings(raw, ("activeSkills", "active_skills"))
    if active_skills is not None:
        kwargs["active_skills"] = active_skills

    command_directories = _command_directories(raw, workspace_root=workspace_root)
    if command_directories is not None:
        kwargs["command_directories"] = command_directories

    runtime_mode = _runtime_mode(raw)
    if runtime_mode is not None:
        kwargs["runtime_mode"] = str(runtime_mode)

    return RuntimeConfig(**kwargs)


def _agent_registry_from_raw(raw: Mapping[str, Any]) -> AgentRegistry | None:
    if "agents" not in raw:
        return None

    agents = raw["agents"]
    default_agent = _first_alias_value(raw, ("defaultAgent", "default_agent"))
    default_agent_name = "general" if default_agent is None else str(default_agent)

    if isinstance(agents, Mapping):
        profiles = [
            _agent_profile_from_mapping(payload, fallback_name=str(name))
            for name, payload in agents.items()
        ]
    elif isinstance(agents, list):
        profiles = [
            _agent_profile_from_mapping(payload, fallback_name=None)
            for payload in agents
        ]
    else:
        raise ValueError("agents must be a mapping or list")

    return AgentRegistry(profiles, default_agent=default_agent_name)


def _command_registry_from_sources(
    *,
    definitions: list[CommandDefinition],
    command_directories: list[str | Path],
) -> CommandRegistry | None:
    if not definitions and not command_directories:
        return None
    return CommandRegistry.from_sources(
        definitions=definitions,
        command_directories=command_directories,
    )


def _agent_profile_from_mapping(
    payload: Any,
    *,
    fallback_name: str | None,
) -> AgentProfile:
    if not isinstance(payload, Mapping):
        raise ValueError("agent entries must be JSON objects")

    metadata = _mapping_copy(payload.get("metadata"))
    extra = {
        str(key): deepcopy(value)
        for key, value in payload.items()
        if str(key) not in _AGENT_KEYS
    }
    if extra:
        metadata.setdefault("raw_config", extra)

    data: dict[str, Any] = {"metadata": metadata}
    if fallback_name is not None:
        data["name"] = fallback_name
    if payload.get("name") is not None:
        data["name"] = str(payload["name"])
    if "name" not in data:
        raise ValueError("agent entries in a list require a name")
    if payload.get("description") is not None:
        data["description"] = str(payload["description"])
    if payload.get("prompt") is not None:
        data["prompt"] = str(payload["prompt"])
    if payload.get("tools") is not None:
        if not isinstance(payload["tools"], Mapping):
            raise ValueError("agent tools must be a mapping")
        data["tools"] = dict(payload["tools"])

    max_iterations = _first_alias_value(payload, ("maxIterations", "max_iterations"))
    if max_iterations is not None:
        data["max_iterations"] = _positive_int(max_iterations, field_name="maxIterations")

    active_skills = _merged_alias_strings(payload, ("skills", "active_skills"))
    if active_skills is not None:
        data["active_skills"] = active_skills

    return AgentProfile(**data)


def _runtime_mode(raw: Mapping[str, Any]) -> Any:
    mode = None
    for key, value in raw.items():
        if key == "runtime" and isinstance(value, Mapping) and value.get("mode") is not None:
            mode = value["mode"]
        elif key == "runtime_mode" and value is not None:
            mode = value
    return mode


def _command_directories(
    raw: Mapping[str, Any],
    *,
    workspace_root: Path,
) -> list[Path] | None:
    paths: list[Path] = []
    default_directory = workspace_root / ".opencode" / "commands"
    if default_directory.is_dir():
        paths.append(default_directory.resolve(strict=False))

    configured = _merged_alias_paths(
        raw,
        ("commandDirectories", "command_directories"),
        workspace_root=workspace_root,
    )
    if configured is not None:
        paths.extend(configured)

    if not paths and configured is None:
        return None
    return _dedupe_paths(paths)


def _loader_metadata(
    raw: Mapping[str, Any],
    loaded_paths: Iterable[Path],
) -> dict[str, Any]:
    loaded_path_strings = [str(path) for path in loaded_paths]
    unconsumed = _unconsumed_config(raw)
    return {
        "loaded_paths": loaded_path_strings,
        "raw_config": deepcopy(dict(raw)),
        "unconsumed_config": unconsumed,
    }


def _unconsumed_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    unconsumed: dict[str, Any] = {}
    for key, value in raw.items():
        key_text = str(key)
        if key_text == "runtime":
            if isinstance(value, Mapping):
                runtime_extra = {
                    str(runtime_key): deepcopy(runtime_value)
                    for runtime_key, runtime_value in value.items()
                    if str(runtime_key) != "mode"
                }
                if runtime_extra:
                    unconsumed[key_text] = runtime_extra
            else:
                unconsumed[key_text] = deepcopy(value)
            continue
        if key_text in _RUNTIME_CONFIG_KEYS:
            continue
        unconsumed[key_text] = deepcopy(value)
    return unconsumed


def _instruction_sources(
    value: Any,
    *,
    workspace_root: Path,
) -> tuple[list[Path], list[str]]:
    paths: list[Path] = []
    texts: list[str] = []

    for entry in _coerce_sequence(value):
        if isinstance(entry, Mapping):
            if "path" in entry:
                paths.extend(
                    _resolve_path_values(workspace_root, entry["path"])
                )
            if "text" in entry:
                texts.extend(_string_values(entry["text"]))
            continue
        if _path_has_value(entry):
            paths.append(_resolve_workspace_path(workspace_root, entry))

    return _dedupe_paths(paths), _dedupe_strings(texts)


def _merged_alias_mapping(
    raw: Mapping[str, Any],
    aliases: Iterable[str],
) -> dict[str, Any] | None:
    alias_set = set(aliases)
    merged: dict[str, Any] = {}
    found = False
    for alias, value in raw.items():
        if alias not in alias_set:
            continue
        found = True
        if value is None:
            merged = {}
            continue
        if not isinstance(value, Mapping):
            raise ValueError(f"{alias} must be a mapping")
        merged = _deep_merge(merged, dict(value))
    return merged if found else None


def _merged_alias_strings(
    raw: Mapping[str, Any],
    aliases: Iterable[str],
) -> list[str] | None:
    alias_set = set(aliases)
    values: list[str] = []
    found = False
    for alias, value in raw.items():
        if alias not in alias_set:
            continue
        found = True
        values.extend(_string_values(value))
    return _dedupe_strings(values) if found else None


def _merged_alias_paths(
    raw: Mapping[str, Any],
    aliases: Iterable[str],
    *,
    workspace_root: Path,
) -> list[Path] | None:
    alias_set = set(aliases)
    paths: list[Path] = []
    found = False
    for alias, value in raw.items():
        if alias not in alias_set:
            continue
        found = True
        paths.extend(_resolve_path_values(workspace_root, value))
    return _dedupe_paths(paths) if found else None


def _resolve_path_values(workspace_root: Path, value: Any) -> list[Path]:
    return [
        _resolve_workspace_path(workspace_root, path)
        for path in _coerce_sequence(value)
        if _path_has_value(path)
    ]


def _string_values(value: Any) -> list[str]:
    values: list[str] = []
    for entry in _coerce_sequence(value):
        text = str(entry).strip()
        if text:
            values.append(text)
    return values


def _first_alias_value(raw: Mapping[str, Any], aliases: Iterable[str]) -> Any:
    alias_set = set(aliases)
    value = None
    for alias, alias_value in raw.items():
        if alias in alias_set and alias_value is not None:
            value = alias_value
    return value


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in override.items():
        if key in merged:
            merged[key] = _merge_value(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _merge_value(base: Any, override: Any) -> Any:
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        return _deep_merge(base, override)
    if isinstance(base, list) and isinstance(override, list):
        return _dedupe_json_values([deepcopy(item) for item in base + override])
    return deepcopy(override)


def _dedupe_json_values(values: Iterable[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = _json_marker(value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
    return deduped


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        marker = str(path)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(path)
    return deduped


def _json_marker(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return repr(value)


def _mapping_copy(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping")
    return dict(deepcopy(value))


def _positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    resolved = int(value)
    if resolved < 1:
        raise ValueError(f"{field_name} must be at least 1")
    return resolved


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _path_has_value(value: Any) -> bool:
    return not (value is None or isinstance(value, str) and not value.strip())


def _workspace_root_path(workspace_root: str | Path) -> Path:
    return Path(workspace_root).expanduser().resolve(strict=False)


def _resolve_workspace_path(workspace_root: Path, path: Any) -> Path:
    raw_path = Path(path).expanduser()
    candidate = raw_path if raw_path.is_absolute() else workspace_root / raw_path
    return candidate.resolve(strict=False)


__all__ = [
    "DEFAULT_CONFIG_FILE_NAMES",
    "RuntimeConfigLoadResult",
    "find_runtime_config_files",
    "load_runtime_config",
]
