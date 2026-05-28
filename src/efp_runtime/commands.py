"""Custom slash command discovery and prompt expansion for Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any


COMMAND_EXTENSIONS = {".md", ".txt"}
COMMAND_METADATA_KEYS = {
    "name",
    "description",
    "argument-hint",
    "agent",
    "model",
    "tools",
}


@dataclass
class CommandDefinition:
    """A prompt template loaded from a configured command file."""

    name: str
    description: str = ""
    content: str = ""
    command_file: Path = field(default_factory=Path)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = _normalize_command_name(self.name)
        self.description = str(self.description or "")
        self.content = str(self.content or "")
        self.command_file = Path(self.command_file)
        self.metadata = dict(self.metadata)


@dataclass
class CommandExpansionResult:
    """The user prompt after expanding a configured slash command."""

    text: str
    definition: CommandDefinition
    arguments: str
    remaining_text: str
    truncated: bool = False
    original_chars: int = 0
    max_chars: int = 0


class CommandRegistry:
    """Discover custom command prompt templates from configured directories."""

    def __init__(self, command_directories: Iterable[str | Path]):
        self.command_directories = [
            Path(directory).expanduser() for directory in command_directories
        ]
        self._commands: dict[str, CommandDefinition] | None = None

    def discover(self, *, refresh: bool = False) -> list[CommandDefinition]:
        if self._commands is None or refresh:
            commands: dict[str, CommandDefinition] = {}
            for command in discover_commands(self.command_directories):
                commands[command.name] = command
            self._commands = commands
        return [self._commands[name] for name in sorted(self._commands)]

    def get(self, name: str, *, refresh: bool = False) -> CommandDefinition | None:
        normalized = _normalize_command_name(name)
        if not normalized:
            return None
        if self._commands is None or refresh:
            self.discover(refresh=refresh)
        assert self._commands is not None
        return self._commands.get(normalized)


def discover_commands(
    command_directories: Iterable[str | Path],
) -> list[CommandDefinition]:
    """Discover command files from configured directories.

    Duplicate command names are resolved by letting later discoveries override
    earlier ones. Directory order is preserved, and files inside each directory
    are processed in stable path order.
    """

    commands: list[CommandDefinition] = []
    for configured_dir in command_directories:
        directory = Path(configured_dir).expanduser()
        if not directory.exists():
            continue
        for command_file in _iter_command_files(directory):
            commands.append(_load_command(command_file, directory))
    return commands


def expand_command(
    text: str,
    registry: CommandRegistry,
    *,
    max_command_chars: int = 20000,
    refresh: bool = True,
) -> CommandExpansionResult | None:
    """Expand the first effective slash command line in ``text``.

    Unknown slash commands are left untouched by returning ``None``.
    """

    if max_command_chars < 0:
        raise ValueError("max_command_chars must be greater than or equal to 0")

    parsed = _parse_first_command_line(text)
    if parsed is None:
        return None

    name, arguments, remaining_text = parsed
    if name == "skill":
        return None

    definition = registry.get(name, refresh=refresh)
    if definition is None:
        return None

    original_chars = len(definition.content)
    truncated = original_chars > max_command_chars
    command_content = definition.content[:max_command_chars]
    expanded_text = _render_expanded_command(
        definition=definition,
        command_content=command_content,
        arguments=arguments,
        remaining_text=remaining_text,
        truncated=truncated,
        original_chars=original_chars,
        max_chars=max_command_chars,
    )
    return CommandExpansionResult(
        text=expanded_text,
        definition=definition,
        arguments=arguments,
        remaining_text=remaining_text,
        truncated=truncated,
        original_chars=original_chars,
        max_chars=max_command_chars,
    )


def _iter_command_files(directory: Path) -> list[Path]:
    if directory.is_file():
        if directory.suffix.lower() in COMMAND_EXTENSIONS:
            return [directory]
        return []
    if not directory.is_dir():
        return []
    candidates = [
        path
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.lower() in COMMAND_EXTENSIONS
        and not _has_hidden_relative_part(path, directory)
    ]
    return sorted(candidates, key=lambda path: str(path.relative_to(directory)))


def _load_command(command_file: Path, command_root: Path) -> CommandDefinition:
    raw_content = command_file.read_text(encoding="utf-8")
    metadata, body = _parse_command_metadata(raw_content)
    fallback_name = _command_name_from_path(command_file, command_root)
    name = _normalize_command_name(str(metadata.get("name") or fallback_name))
    metadata = dict(metadata)
    metadata["name"] = name
    description = str(metadata.get("description") or "").strip()
    return CommandDefinition(
        name=name,
        description=description,
        content=body.strip("\n"),
        command_file=command_file,
        metadata=metadata,
    )


def _command_name_from_path(command_file: Path, command_root: Path) -> str:
    if command_root.is_file():
        relative = Path(command_file.name)
    else:
        try:
            relative = command_file.relative_to(command_root)
        except ValueError:
            relative = Path(command_file.name)
    parts = list(relative.with_suffix("").parts)
    return ":".join(parts)


def _parse_command_metadata(content: str) -> tuple[dict[str, Any], str]:
    lines = content.splitlines()
    if not lines:
        return {}, ""

    if lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                metadata = _parse_simple_yaml_lines(lines[1:index])
                body = "\n".join(lines[index + 1 :])
                return metadata, body
        return {}, content

    metadata_lines: list[str] = []
    body_start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            body_start = index + 1
            break
        if ":" not in stripped:
            if metadata_lines:
                body_start = index
                break
            metadata_lines = []
            body_start = 0
            break
        key = stripped.split(":", 1)[0].strip()
        if key not in COMMAND_METADATA_KEYS:
            if metadata_lines:
                body_start = index
                break
            metadata_lines = []
            body_start = 0
            break
        metadata_lines.append(line)

    if metadata_lines:
        return _parse_simple_yaml_lines(metadata_lines), "\n".join(lines[body_start:])
    return {}, content


def _parse_simple_yaml_lines(lines: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if key not in COMMAND_METADATA_KEYS:
            continue
        metadata[key] = _parse_metadata_value(key, value.strip())
    return metadata


def _parse_metadata_value(key: str, value: str) -> Any:
    if key == "tools":
        return _parse_tools_value(value)
    return _strip_yaml_string(value)


def _parse_tools_value(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1]
    return [
        _strip_yaml_string(part.strip())
        for part in stripped.split(",")
        if part.strip()
    ]


def _strip_yaml_string(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_first_command_line(text: str) -> tuple[str, str, str] | None:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("/"):
            return None
        command_text = stripped[1:]
        if not command_text:
            return None
        if command_text[0].isspace():
            return None
        parts = command_text.split(None, 1)
        name = _normalize_command_name(parts[0])
        arguments = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            return None
        remaining_text = "".join(lines[index + 1 :])
        return name, arguments, remaining_text
    return None


def _render_expanded_command(
    *,
    definition: CommandDefinition,
    command_content: str,
    arguments: str,
    remaining_text: str,
    truncated: bool,
    original_chars: int,
    max_chars: int,
) -> str:
    command_attrs = [
        f'name="{escape(definition.name, quote=True)}"',
        f'source="{escape(str(definition.command_file), quote=True)}"',
    ]
    if truncated:
        command_attrs.extend(
            [
                'truncated="true"',
                f'original_chars="{original_chars}"',
                f'max_chars="{max_chars}"',
            ]
        )
    parts = [
        f"<command {' '.join(command_attrs)}>",
        command_content,
        "</command>",
    ]
    if truncated:
        parts.extend(
            [
                (
                    f'<command_truncated original_chars="{original_chars}" '
                    f'max_chars="{max_chars}">'
                ),
                "Command content was truncated to max_command_chars.",
                "</command_truncated>",
            ]
        )
    parts.extend(
        [
            "<command_arguments>",
            arguments,
            "</command_arguments>",
            "<command_input>",
            remaining_text,
            "</command_input>",
        ]
    )
    return "\n".join(parts)


def _normalize_command_name(name: str) -> str:
    return str(name or "").strip().lstrip("/")


def _has_hidden_relative_part(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return any(part.startswith(".") for part in relative.parts)


__all__ = [
    "CommandDefinition",
    "CommandExpansionResult",
    "CommandRegistry",
    "discover_commands",
    "expand_command",
]
