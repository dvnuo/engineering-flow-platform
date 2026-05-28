"""Workspace configuration loading for Runtime v2.

The loader is intentionally side-effect free: it reads local JSON/JSONC files
and returns Runtime v2 config objects without starting tool providers or LLM
integrations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any

from .agents.discovery import (
    DEFAULT_AGENT_DIRECTORIES,
    agent_name_from_mapping,
    agent_profile_from_mapping,
    discover_agent_profiles,
)
from .agents.defaults import default_agent_profiles
from .agents.profile import AgentProfile
from .agents.registry import AgentRegistry
from .commands import (
    CommandDefinition,
    CommandRegistry,
    builtin_command_definitions,
    command_definitions_from_config,
)
from .runtime.config import RuntimeConfig
from .skills.discovery import SkillDiscovery, default_skill_directories


DEFAULT_CONFIG_FILE_NAMES = (
    "opencode.json",
    "opencode.jsonc",
    ".opencode.json",
    ".opencode/config.json",
    ".opencode/config.jsonc",
)

_RUNTIME_PROJECT_MARKER_DIRECTORIES = (
    ".opencode/command",
    ".opencode/commands",
    ".opencode/skill",
    ".opencode/skills",
    ".opencode/agents",
)

_RUNTIME_COMPATIBILITY_SKILL_MARKER_DIRECTORIES = (
    ".claude/skills",
    ".agents/skills",
)

_RUNTIME_CONFIG_KEYS = {
    "permission",
    "permissions",
    "disabledTools",
    "disabled_tools",
    "enabledTools",
    "enabled_tools",
    "modelAwareToolSelection",
    "model_aware_tool_selection",
    "instructions",
    "systemPrompt",
    "system_prompt",
    "skills",
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
    "agent",
    "agents",
    "agentDirectories",
    "agent_directories",
    "defaultAgent",
    "default_agent",
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


def resolve_runtime_workspace_root(
    start: str | Path,
    *,
    include_defaults: bool = True,
) -> Path:
    """Resolve the Runtime v2 project root for a startup path."""

    resolved_start = _workspace_root_path(start)
    if not include_defaults:
        return resolved_start

    search_start = resolved_start.parent if resolved_start.is_file() else resolved_start
    for directory in _self_and_parents(search_start):
        if _has_runtime_project_marker(directory):
            return directory
    return search_start


def find_runtime_config_files(
    workspace_root: str | Path,
    *,
    include_defaults: bool = True,
) -> list[Path]:
    """Return existing default Runtime v2 config files in load order."""

    if not include_defaults:
        return []
    root = resolve_runtime_workspace_root(
        workspace_root,
        include_defaults=include_defaults,
    )
    return _default_runtime_config_files(root)


def _default_runtime_config_files(workspace_root: Path) -> list[Path]:
    paths: list[Path] = []
    for name in DEFAULT_CONFIG_FILE_NAMES:
        path = _resolve_workspace_path(workspace_root, name)
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

    root = resolve_runtime_workspace_root(
        workspace_root,
        include_defaults=include_defaults,
    )
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
    config = _runtime_config_from_raw(
        raw,
        root,
        metadata,
        include_defaults=include_defaults,
    )
    agent_registry = _agent_registry_from_raw(
        raw,
        workspace_root=root,
        include_defaults=include_defaults,
    )
    command_definitions = command_definitions_from_config(raw)
    command_registry = _command_registry_from_sources(
        workspace_root=root,
        definitions=command_definitions,
        command_directories=config.command_directories,
        skill_directories=config.skill_directories,
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
        candidates.extend(_default_runtime_config_files(workspace_root))
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
        text = _substitute_config_variables(text, path)
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


def _substitute_config_variables(text: str, path: Path) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    length = len(text)

    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""

        if in_string:
            if not escaped:
                token_end = _config_variable_token_end(text, index)
                if token_end is not None:
                    token = text[index:token_end]
                    value = _config_variable_value(token, path)
                    output.append(_json_string_fragment(value))
                    index = token_end
                    continue

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
            while index < length and text[index] not in "\r\n":
                output.append(text[index])
                index += 1
            continue

        if char == "/" and next_char == "*":
            output.extend((char, next_char))
            index += 2
            while index < length:
                output.append(text[index])
                if text[index] == "*" and index + 1 < length and text[index + 1] == "/":
                    output.append(text[index + 1])
                    index += 2
                    break
                index += 1
            continue

        token_end = _config_variable_token_end(text, index)
        if token_end is not None:
            token = text[index:token_end]
            output.append(_config_variable_value(token, path))
            index = token_end
            continue

        output.append(char)
        index += 1

    return "".join(output)


def _config_variable_token_end(text: str, start: int) -> int | None:
    if not (text.startswith("{env:", start) or text.startswith("{file:", start)):
        return None
    end = text.find("}", start + 1)
    if end < 0:
        return None
    return end + 1


def _config_variable_value(token: str, path: Path) -> str:
    if token.startswith("{env:"):
        return os.environ.get(token[5:-1], "")
    if token.startswith("{file:"):
        resolved = _resolve_config_file_reference(token[6:-1], path)
        if not resolved.is_file():
            raise ValueError(
                f"Config variable {token!r} in {path} "
                f"references missing file {resolved}"
            )
        try:
            return resolved.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(
                f"Config variable {token!r} in {path} "
                f"could not read file {resolved}: {exc}"
            ) from exc
    return token


def _resolve_config_file_reference(reference: str, path: Path) -> Path:
    raw_path = Path(reference.strip()).expanduser()
    candidate = raw_path if raw_path.is_absolute() else path.parent / raw_path
    return candidate.resolve(strict=False)


def _json_string_fragment(value: str) -> str:
    return json.dumps(value)[1:-1]


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
    *,
    include_defaults: bool,
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

    model_aware_tool_selection = _model_aware_tool_selection(raw)
    if model_aware_tool_selection is not None:
        kwargs["model_aware_tool_selection"] = model_aware_tool_selection

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

    default_skill_dirs = default_skill_directories(
        workspace_root,
        include_defaults=include_defaults,
    )
    configured_skill_directories = _merged_alias_paths(
        raw,
        ("skillDirectories", "skill_directories"),
        workspace_root=workspace_root,
    )
    if configured_skill_directories is not None:
        default_skill_dirs.extend(configured_skill_directories)
    skills_paths = _skills_paths(raw, workspace_root=workspace_root)
    if skills_paths is not None:
        default_skill_dirs.extend(skills_paths)
    if (
        default_skill_dirs
        or configured_skill_directories is not None
        or skills_paths is not None
    ):
        kwargs["skill_directories"] = _dedupe_paths(default_skill_dirs)

    active_skills = _merged_alias_strings(raw, ("activeSkills", "active_skills"))
    if active_skills is not None:
        kwargs["active_skills"] = active_skills

    command_directories = _command_directories(
        raw,
        workspace_root=workspace_root,
        include_defaults=include_defaults,
    )
    if command_directories is not None:
        kwargs["command_directories"] = command_directories

    runtime_mode = _runtime_mode(raw)
    if runtime_mode is not None:
        kwargs["runtime_mode"] = str(runtime_mode)

    return RuntimeConfig(**kwargs)


def _agent_registry_from_raw(
    raw: Mapping[str, Any],
    *,
    workspace_root: Path,
    include_defaults: bool,
) -> AgentRegistry | None:
    default_agent = _first_alias_value(raw, ("defaultAgent", "default_agent"))
    if default_agent is not None:
        default_agent_name = str(default_agent)
    elif include_defaults:
        default_agent_name = "general"
    else:
        default_agent_name = None

    profiles_by_name: dict[str, AgentProfile] = {}
    if include_defaults:
        for profile in default_agent_profiles():
            _replace_agent_profile(profiles_by_name, profile)

    for profile in discover_agent_profiles(
        _agent_directories_from_raw(
            raw,
            workspace_root=workspace_root,
            include_defaults=include_defaults,
        )
    ):
        _replace_agent_profile(profiles_by_name, profile)

    for alias, agents in raw.items():
        if alias not in {"agent", "agents"}:
            continue
        for fallback_name, payload in _agent_config_entries(agents, alias=str(alias)):
            profile_name = agent_name_from_mapping(payload, fallback_name=fallback_name)
            profile = agent_profile_from_mapping(payload, fallback_name=fallback_name)
            if profile is None:
                profiles_by_name.pop(profile_name, None)
                continue
            _replace_agent_profile(profiles_by_name, profile)

    if not profiles_by_name:
        return None
    return AgentRegistry(profiles_by_name.values(), default_agent=default_agent_name)


def _agent_directories_from_raw(
    raw: Mapping[str, Any],
    *,
    workspace_root: Path,
    include_defaults: bool,
) -> list[Path]:
    directories: list[Path] = []
    if include_defaults:
        for directory in DEFAULT_AGENT_DIRECTORIES:
            path = _resolve_workspace_path(workspace_root, directory)
            if path.is_dir():
                directories.append(path)

    configured = _merged_alias_paths(
        raw,
        ("agentDirectories", "agent_directories"),
        workspace_root=workspace_root,
    )
    if configured is not None:
        directories.extend(configured)
    return _dedupe_paths(directories)


def _agent_config_entries(
    agents: Any,
    *,
    alias: str,
) -> list[tuple[str | None, Mapping[str, Any]]]:
    if isinstance(agents, Mapping):
        if alias == "agent" and _looks_like_single_agent_entry(agents):
            return [(None, agents)]
        return [
            (str(name), _require_agent_mapping(payload))
            for name, payload in agents.items()
        ]
    if isinstance(agents, list):
        return [(None, _require_agent_mapping(payload)) for payload in agents]
    raise ValueError(f"{alias} must be a mapping or list")


def _command_registry_from_sources(
    *,
    workspace_root: str | Path | None,
    definitions: list[CommandDefinition],
    command_directories: list[str | Path],
    skill_directories: list[str | Path],
) -> CommandRegistry | None:
    return CommandRegistry.from_sources(
        definitions=[
            *builtin_command_definitions(workspace_root),
            *definitions,
        ],
        command_directories=command_directories,
        skill_discovery=(
            SkillDiscovery(skill_directories) if skill_directories else None
        ),
    )


def _require_agent_mapping(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("agent entries must be JSON objects")
    return payload


def _looks_like_single_agent_entry(value: Mapping[str, Any]) -> bool:
    return "name" in value


def _replace_agent_profile(
    profiles: dict[str, AgentProfile],
    profile: AgentProfile,
) -> None:
    if profile.name in profiles:
        del profiles[profile.name]
    profiles[profile.name] = profile


def _runtime_mode(raw: Mapping[str, Any]) -> Any:
    mode = None
    for key, value in raw.items():
        if key == "runtime" and isinstance(value, Mapping) and value.get("mode") is not None:
            mode = value["mode"]
        elif key == "runtime_mode" and value is not None:
            mode = value
    return mode


def _model_aware_tool_selection(raw: Mapping[str, Any]) -> Any:
    selection = None
    for key, value in raw.items():
        if key == "runtime" and isinstance(value, Mapping):
            for nested_key in (
                "modelAwareToolSelection",
                "model_aware_tool_selection",
            ):
                if nested_key in value:
                    selection = value[nested_key]
        elif key in {"modelAwareToolSelection", "model_aware_tool_selection"}:
            selection = value
    return selection


def _command_directories(
    raw: Mapping[str, Any],
    *,
    workspace_root: Path,
    include_defaults: bool,
) -> list[Path] | None:
    paths = default_command_directories(
        workspace_root,
        include_defaults=include_defaults,
    )

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


def default_command_directories(
    workspace_root: str | Path,
    *,
    include_defaults: bool = True,
) -> list[Path]:
    """Return existing default command directories in load order."""

    if not include_defaults:
        return []
    root = _workspace_root_path(workspace_root)
    directories: list[Path] = []
    global_directory = Path("~/.config/opencode/commands").expanduser().resolve(
        strict=False,
    )
    if global_directory.is_dir():
        directories.append(global_directory)
    for default_directory in (
        root / ".opencode" / "command",
        root / ".opencode" / "commands",
    ):
        path = default_directory.resolve(strict=False)
        if path.is_dir():
            directories.append(path)
    return directories


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
                    if str(runtime_key)
                    not in {
                        "mode",
                        "modelAwareToolSelection",
                        "model_aware_tool_selection",
                    }
                }
                if runtime_extra:
                    unconsumed[key_text] = runtime_extra
            else:
                unconsumed[key_text] = deepcopy(value)
            continue
        if key_text == "skills":
            skills_extra = _unconsumed_skills_config(value)
            if skills_extra:
                unconsumed[key_text] = skills_extra
            continue
        if key_text in _RUNTIME_CONFIG_KEYS:
            continue
        unconsumed[key_text] = deepcopy(value)
    return unconsumed


def _skills_paths(
    raw: Mapping[str, Any],
    *,
    workspace_root: Path,
) -> list[Path] | None:
    if "skills" not in raw:
        return None
    skills = raw["skills"]
    if not isinstance(skills, Mapping):
        raise ValueError("skills must be an object")
    if "paths" not in skills:
        return None
    return _dedupe_paths(_resolve_path_values(workspace_root, skills["paths"]))


def _unconsumed_skills_config(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("skills must be an object")
    return {
        str(skills_key): deepcopy(skills_value)
        for skills_key, skills_value in value.items()
        if str(skills_key) != "paths"
    }


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


def _self_and_parents(path: Path) -> Iterable[Path]:
    yield path
    yield from path.parents


def _has_runtime_project_marker(directory: Path) -> bool:
    for name in DEFAULT_CONFIG_FILE_NAMES:
        if _resolve_workspace_path(directory, name).is_file():
            return True
    for marker in _RUNTIME_PROJECT_MARKER_DIRECTORIES:
        if _resolve_workspace_path(directory, marker).is_dir():
            return True
    if _is_user_home_directory(directory):
        return False
    for marker in _RUNTIME_COMPATIBILITY_SKILL_MARKER_DIRECTORIES:
        if _resolve_workspace_path(directory, marker).is_dir():
            return True
    return False


def _is_user_home_directory(directory: Path) -> bool:
    return directory.resolve(strict=False) == Path.home().expanduser().resolve(
        strict=False,
    )


def _workspace_root_path(workspace_root: str | Path) -> Path:
    return Path(workspace_root).expanduser().resolve(strict=False)


def _resolve_workspace_path(workspace_root: Path, path: Any) -> Path:
    raw_path = Path(path).expanduser()
    candidate = raw_path if raw_path.is_absolute() else workspace_root / raw_path
    return candidate.resolve(strict=False)


__all__ = [
    "DEFAULT_CONFIG_FILE_NAMES",
    "RuntimeConfigLoadResult",
    "default_command_directories",
    "find_runtime_config_files",
    "load_runtime_config",
    "resolve_runtime_workspace_root",
]
