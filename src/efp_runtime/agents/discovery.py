"""Markdown agent discovery for Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from .profile import AgentProfile
from .registry import AgentRegistry


DEFAULT_AGENT_DIRECTORIES = (".opencode/agent", ".opencode/agents")
DEFAULT_MODE_DIRECTORIES = (".opencode/mode", ".opencode/modes")
AGENT_MARKDOWN_EXTENSIONS = {".md", ".markdown"}

_MAX_ITERATION_ALIASES = ("maxIterations", "max_iterations", "steps", "maxSteps")
_SKILL_ALIASES = ("skills", "active_skills")
_DISABLED_ALIASES = ("disable", "disabled")
_METADATA_ALIASES = {
    "mode": "mode",
    "model": "model",
    "temperature": "temperature",
    "top_p": "top_p",
    "topP": "top_p",
    "permission": "permission",
    "task": "task",
    "hidden": "hidden",
    "color": "color",
    "disable": "disabled",
    "disabled": "disabled",
}
_PROFILE_KEYS = {
    "name",
    "description",
    "prompt",
    "tools",
    "maxIterations",
    "max_iterations",
    "steps",
    "maxSteps",
    "skills",
    "active_skills",
    "metadata",
}
_KNOWN_AGENT_KEYS = _PROFILE_KEYS | set(_METADATA_ALIASES)
_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
_FLOAT_PATTERN = re.compile(
    r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)$|^[+-]?(?:\d+\.\d*|\.\d+)$"
)


@dataclass(frozen=True)
class MarkdownAgentDocument:
    """Parsed markdown agent file content."""

    path: Path
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""


@dataclass(frozen=True)
class AgentProfileDiscoverySource:
    """Markdown profile discovery source and scan behavior."""

    directory: str | Path
    recursive: bool = True
    forced_mode: str | None = None


def discover_agent_profiles(agent_directories: Iterable[str | Path]) -> list[AgentProfile]:
    """Discover markdown agent profiles from configured directories.

    Directory order is preserved, files inside each directory are processed in
    stable relative-path order, and later discoveries replace earlier profiles
    with the same agent name.
    """

    return discover_agent_profiles_from_sources(
        AgentProfileDiscoverySource(directory) for directory in agent_directories
    )


def discover_agent_profiles_from_sources(
    sources: Iterable[AgentProfileDiscoverySource],
) -> list[AgentProfile]:
    """Discover markdown agent profiles from ordered profile sources."""

    profiles: dict[str, AgentProfile] = {}
    for source in sources:
        directory = Path(source.directory).expanduser()
        for agent_file in _iter_agent_files(directory, recursive=source.recursive):
            fallback_name = _agent_name_from_path(agent_file)
            document = load_markdown_agent_document(agent_file)
            profile_name = agent_name_from_mapping(
                document.frontmatter,
                fallback_name=fallback_name,
            )
            profile = agent_profile_from_mapping(
                document.frontmatter,
                fallback_name=fallback_name,
                prompt=document.body.strip("\n"),
            )
            if profile is None:
                profiles.pop(profile_name, None)
                continue
            if source.forced_mode is not None:
                profile.metadata["mode"] = source.forced_mode
            _replace_profile(profiles, profile)
    return list(profiles.values())


def load_agent_registry(
    agent_directories: Iterable[str | Path],
    *,
    profiles: Iterable[AgentProfile] | None = None,
    default_agent: str | None = "general",
) -> AgentRegistry:
    """Return an ``AgentRegistry`` from discovered and explicit profiles."""

    merged: dict[str, AgentProfile] = {}
    for profile in discover_agent_profiles(agent_directories):
        _replace_profile(merged, profile)
    for profile in profiles or []:
        _replace_profile(merged, profile)
    return AgentRegistry(merged.values(), default_agent=default_agent)


def load_markdown_agent_document(path: str | Path) -> MarkdownAgentDocument:
    """Read and parse a markdown agent file."""

    agent_file = Path(path).expanduser()
    content = agent_file.read_text(encoding="utf-8")
    frontmatter, body = parse_markdown_agent(content)
    return MarkdownAgentDocument(
        path=agent_file,
        frontmatter=frontmatter,
        body=body,
    )


def parse_markdown_agent(content: str) -> tuple[dict[str, Any], str]:
    """Parse optional ``---`` frontmatter and return ``(config, body)``."""

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            frontmatter = _parse_frontmatter_lines(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            return frontmatter, body

    return {}, content


def agent_profile_from_mapping(
    payload: Mapping[str, Any],
    *,
    fallback_name: str | None = None,
    prompt: str | None = None,
) -> AgentProfile | None:
    """Convert opencode-style agent config into an ``AgentProfile``.

    ``None`` is returned when the entry is explicitly disabled.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("agent entries must be mappings")

    if is_agent_disabled(payload):
        return None

    metadata = _metadata_from_mapping(payload)
    data: dict[str, Any] = {"metadata": metadata}

    name = agent_name_from_mapping(payload, fallback_name=fallback_name)
    data["name"] = name

    if payload.get("description") is not None:
        data["description"] = str(payload["description"])

    profile_prompt = prompt if prompt is not None and prompt.strip() else payload.get("prompt")
    if profile_prompt is not None:
        data["prompt"] = str(profile_prompt)

    if payload.get("tools") is not None:
        tools = _tools_from_value(payload["tools"])
        if tools is None:
            _record_raw_config(metadata, {"tools": deepcopy(payload["tools"])})
        else:
            data["tools"] = tools

    max_iterations = _first_alias_value(payload, _MAX_ITERATION_ALIASES)
    if max_iterations is not None:
        data["max_iterations"] = _positive_int(
            max_iterations,
            field_name="maxIterations",
        )

    active_skills = _merged_alias_strings(payload, _SKILL_ALIASES)
    if active_skills is not None:
        data["active_skills"] = active_skills

    return AgentProfile(**data)


def agent_name_from_mapping(
    payload: Mapping[str, Any],
    *,
    fallback_name: str | None = None,
) -> str:
    """Resolve the effective agent name from config and fallback source name."""

    if not isinstance(payload, Mapping):
        raise ValueError("agent entries must be mappings")
    value = payload.get("name", fallback_name)
    name = str(value or "").strip()
    if not name:
        raise ValueError("agent profile name is required")
    return name


def is_agent_disabled(payload: Mapping[str, Any]) -> bool:
    """Return whether an agent entry is disabled."""

    value = _first_alias_value(payload, _DISABLED_ALIASES)
    return _bool_from_value(value) is True


def _iter_agent_files(directory: Path, *, recursive: bool) -> list[Path]:
    if directory.is_file():
        if directory.suffix.lower() in AGENT_MARKDOWN_EXTENSIONS:
            return [directory]
        return []
    if not directory.is_dir():
        return []

    paths = directory.rglob("*") if recursive else directory.iterdir()
    candidates = [
        path
        for path in paths
        if path.is_file()
        and path.suffix.lower() in AGENT_MARKDOWN_EXTENSIONS
        and not _has_hidden_relative_directory(path, directory)
    ]
    return sorted(candidates, key=lambda path: str(path.relative_to(directory)))


def _agent_name_from_path(path: Path) -> str:
    return path.stem.strip()


def _metadata_from_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _mapping_copy(payload.get("metadata"))
    for source_key, target_key in _METADATA_ALIASES.items():
        if source_key in payload:
            metadata[target_key] = deepcopy(payload[source_key])
    metadata.setdefault("mode", "all")

    extra = {
        str(key): deepcopy(value)
        for key, value in payload.items()
        if str(key) not in _KNOWN_AGENT_KEYS
    }
    if extra:
        _record_raw_config(metadata, extra)
    return metadata


def _record_raw_config(metadata: dict[str, Any], extra: Mapping[str, Any]) -> None:
    existing = metadata.get("raw_config")
    raw_config = dict(existing) if isinstance(existing, Mapping) else {}
    for key, value in extra.items():
        raw_config[str(key)] = deepcopy(value)
    metadata["raw_config"] = raw_config


def _tools_from_value(value: Any) -> dict[str, bool] | None:
    if isinstance(value, Mapping):
        tools: dict[str, bool] = {}
        for tool_name, enabled in value.items():
            resolved = _bool_from_value(enabled)
            if resolved is None:
                raise ValueError("agent tools must map tool ids to bool values")
            tools[str(tool_name)] = resolved
        return tools
    if isinstance(value, (list, tuple)):
        tools = {}
        for entry in value:
            name = str(entry).strip()
            if name:
                tools[name] = True
        return tools
    return None


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if _indent_width(line) != 0 or ":" not in stripped:
            index += 1
            continue

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            parsed[key] = _parse_frontmatter_value(value)
            index += 1
            continue

        nested_lines: list[str] = []
        index += 1
        while index < len(lines):
            nested_line = lines[index]
            nested_stripped = nested_line.strip()
            if not nested_stripped or nested_stripped.startswith("#"):
                index += 1
                continue
            if _indent_width(nested_line) == 0:
                break
            nested_lines.append(nested_line)
            index += 1
        parsed[key] = _parse_nested_mapping(nested_lines)
    return parsed


def _parse_nested_mapping(lines: list[str]) -> Any:
    nested: dict[str, Any] = {}
    raw_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        raw_lines.append(stripped)
        if stripped.startswith("- ") or ":" not in stripped:
            return "\n".join(raw_lines)
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            return "\n".join(raw_lines)
        nested[key] = _parse_frontmatter_value(value)
    return nested


def _parse_frontmatter_value(value: str) -> Any:
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return [_parse_frontmatter_value(part) for part in _split_inline_list(stripped[1:-1])]

    unquoted = _strip_yaml_string(stripped)
    if unquoted != stripped:
        return unquoted

    bool_value = _bool_from_value(stripped)
    if bool_value is not None:
        return bool_value

    lowered = stripped.lower()
    if lowered in {"null", "none", "~"}:
        return None
    if _INTEGER_PATTERN.match(stripped):
        try:
            return int(stripped)
        except ValueError:
            return stripped
    if _FLOAT_PATTERN.match(stripped):
        try:
            return float(stripped)
        except ValueError:
            return stripped
    return stripped


def _split_inline_list(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if quote and char == "\\":
            current.append(char)
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            current.append(char)
            continue
        if char == "," and quote is None:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _strip_yaml_string(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _bool_from_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def _first_alias_value(raw: Mapping[str, Any], aliases: Iterable[str]) -> Any:
    alias_set = set(aliases)
    value = None
    for alias, alias_value in raw.items():
        if alias in alias_set and alias_value is not None:
            value = alias_value
    return value


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


def _string_values(value: Any) -> list[str]:
    values: list[str] = []
    for entry in _coerce_sequence(value):
        text = str(entry).strip()
        if text:
            values.append(text)
    return values


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


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


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _has_hidden_relative_directory(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return any(part.startswith(".") for part in relative.parts[:-1])


def _replace_profile(profiles: dict[str, AgentProfile], profile: AgentProfile) -> None:
    if not isinstance(profile, AgentProfile):
        raise TypeError("profile must be an AgentProfile")
    if profile.name in profiles:
        del profiles[profile.name]
    profiles[profile.name] = profile


__all__ = [
    "DEFAULT_AGENT_DIRECTORIES",
    "DEFAULT_MODE_DIRECTORIES",
    "AgentProfileDiscoverySource",
    "MarkdownAgentDocument",
    "agent_name_from_mapping",
    "agent_profile_from_mapping",
    "discover_agent_profiles",
    "discover_agent_profiles_from_sources",
    "is_agent_disabled",
    "load_agent_registry",
    "load_markdown_agent_document",
    "parse_markdown_agent",
]
