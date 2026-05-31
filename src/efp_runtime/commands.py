"""Custom slash command discovery and prompt expansion for EFP runtime."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from html import escape
from pathlib import Path
import re
import shlex
from typing import Any

from .skills.discovery import SkillDiscovery
from .types import SkillPackage


COMMAND_EXTENSIONS = {".md", ".txt"}
COMMAND_METADATA_KEYS = {
    "name",
    "description",
    "argument-hint",
    "argument_hint",
    "argumentHint",
    "agent",
    "model",
    "subtask",
    "tools",
}
CONFIG_COMMAND_ALIASES = {"command", "commands"}
CONFIG_TEMPLATE_KEYS = ("template", "content")
TEMPLATE_VARIABLE_RE = re.compile(r"\$(ARGUMENTS|[1-9][0-9]*)(?![A-Za-z0-9_])")


def builtin_command_definitions(
    workspace_root: str | Path | None,
) -> list["CommandDefinition"]:
    """Return EFP runtime built-in slash commands in base registration order."""

    root_text = _builtin_workspace_root_text(workspace_root)
    agents_path = f"{root_text}/AGENTS.md" if root_text != "." else "./AGENTS.md"
    return [
        CommandDefinition(
            name="init",
            description="Create or update AGENTS.md for this workspace",
            argument_hint="[focus]",
            content=_builtin_init_template(
                workspace_root=root_text,
                agents_path=agents_path,
            ),
            source="builtin",
        ),
        CommandDefinition(
            name="review",
            description="Review working tree, commit, branch, or PR changes",
            argument_hint="[commit|branch|PR|URL]",
            content=_builtin_review_template(),
            source="builtin",
            subtask=True,
        ),
    ]


@dataclass
class CommandDefinition:
    """A prompt template loaded from a configured command file."""

    name: str
    description: str = ""
    content: str = ""
    command_file: Path = field(default_factory=Path)
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "file"
    argument_hint: str | None = None
    agent: str | None = None
    model: str | None = None
    subtask: Any = None

    def __post_init__(self) -> None:
        self.name = _normalize_command_name(self.name)
        self.metadata = dict(deepcopy(self.metadata))
        self.source = str(self.source or self.metadata.get("source") or "file")
        self.metadata["source"] = self.source
        self.metadata["name"] = self.name
        if self.description:
            self.description = str(self.description)
        elif self.metadata.get("description") is not None:
            self.description = str(self.metadata["description"])
        else:
            self.description = ""
        if self.description:
            self.metadata["description"] = self.description
        self.content = str(self.content or "")
        self.command_file = Path(self.command_file)
        self.argument_hint = _metadata_string_value(
            self.argument_hint,
            self.metadata,
            ("argument-hint", "argument_hint", "argumentHint"),
        )
        if self.argument_hint is not None:
            self.metadata["argument-hint"] = self.argument_hint
        self.agent = _metadata_string_value(self.agent, self.metadata, ("agent",))
        if self.agent is not None:
            self.metadata["agent"] = self.agent
        self.model = _metadata_string_value(self.model, self.metadata, ("model",))
        if self.model is not None:
            self.metadata["model"] = self.model
        if self.subtask is None and "subtask" in self.metadata:
            self.subtask = deepcopy(self.metadata["subtask"])
        if self.subtask is not None:
            self.metadata["subtask"] = deepcopy(self.subtask)


@dataclass
class CommandInfo:
    """Safe listing view for a slash command.

    This view is intended for UI, CLI, and service routing. It deliberately
    omits the template content used by command expansion.
    """

    name: str
    description: str = ""
    source: str = ""
    argument_hint: str | None = None
    agent: str | None = None
    model: str | None = None
    subtask: Any = None
    tools: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    command_file: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _RenderedCommandTemplate:
    text: str
    template_mask: tuple[bool, ...]


@dataclass(frozen=True)
class CommandShellInterpolation:
    """A shell interpolation span found in rendered command template content."""

    index: int
    command: str
    start: int
    end: int


@dataclass(frozen=True)
class CommandShellExecutionResult:
    """A normalized shell interpolation result ready for prompt rendering."""

    interpolation: CommandShellInterpolation
    tool_id: str
    tool_call_id: str
    status: str
    success: bool
    content: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "index": self.interpolation.index,
            "command": self.interpolation.command,
            "tool_id": self.tool_id,
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "success": self.success,
        }


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
    command_content: str = ""
    command_template_mask: tuple[bool, ...] = field(default_factory=tuple, repr=False)
    command_shell_interpolations: list[dict[str, Any]] = field(default_factory=list)


class CommandRegistry:
    """Discover custom command prompt templates from configured directories."""

    def __init__(
        self,
        command_directories: Iterable[str | Path] | None = None,
        *,
        definitions: Iterable[CommandDefinition] | None = None,
        commands: Iterable[CommandDefinition] | None = None,
        skill_discovery: SkillDiscovery | None = None,
    ):
        self.command_directories = [
            Path(directory).expanduser()
            for directory in (command_directories or [])
        ]
        self.definitions = list(definitions or [])
        if commands is not None:
            self.definitions.extend(commands)
        self.skill_discovery = skill_discovery
        self._commands: dict[str, CommandDefinition] | None = None

    @classmethod
    def from_sources(
        cls,
        *,
        definitions: Iterable[CommandDefinition] | None = None,
        commands: Iterable[CommandDefinition] | None = None,
        command_directories: Iterable[str | Path] | None = None,
        skill_discovery: SkillDiscovery | None = None,
    ) -> "CommandRegistry":
        return cls(
            command_directories=command_directories,
            definitions=definitions,
            commands=commands,
            skill_discovery=skill_discovery,
        )

    def discover(self, *, refresh: bool = False) -> list[CommandDefinition]:
        if self._commands is None or refresh:
            commands: dict[str, CommandDefinition] = {}
            for command in self.definitions:
                commands[command.name] = command
            for command in discover_commands(self.command_directories):
                commands[command.name] = command
            for command in self._discover_skill_commands(refresh=refresh):
                if command.name and command.name not in commands:
                    commands[command.name] = command
            self._commands = commands
        return [self._commands[name] for name in sorted(self._commands)]

    def list(self, *, refresh: bool = False) -> list[CommandInfo]:
        return [
            _command_info_from_definition(command)
            for command in self.discover(refresh=refresh)
        ]

    def get(self, name: str, *, refresh: bool = False) -> CommandDefinition | None:
        normalized = _normalize_command_name(name)
        if not normalized:
            return None
        if self._commands is None or refresh:
            self.discover(refresh=refresh)
        assert self._commands is not None
        return self._commands.get(normalized)

    def _discover_skill_commands(
        self,
        *,
        refresh: bool,
    ) -> list[CommandDefinition]:
        if self.skill_discovery is None:
            return []
        return [
            _command_definition_from_skill(skill)
            for skill in self.skill_discovery.discover(refresh=refresh)
        ]


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


def command_template_hints(template: str) -> list[str]:
    """Return display hints for command template variables."""

    positional: set[int] = set()
    has_arguments = False
    for match in TEMPLATE_VARIABLE_RE.finditer(str(template or "")):
        variable = match.group(1)
        if variable == "ARGUMENTS":
            has_arguments = True
        else:
            positional.add(int(variable))

    hints = [f"${index}" for index in sorted(positional)]
    if has_arguments:
        hints.append("$ARGUMENTS")
    return hints


def command_definitions_from_config(
    raw: Mapping[str, Any],
    *,
    source: str = "config",
) -> list[CommandDefinition]:
    """Build command definitions from opencode-style config mappings.

    Both ``command`` and the compatible ``commands`` alias are accepted. When
    both aliases appear, entries are emitted in config key order so registry
    composition can apply the same later-definition-wins semantics as files.
    """

    definitions: list[CommandDefinition] = []
    for key, value in raw.items():
        if str(key) not in CONFIG_COMMAND_ALIASES:
            continue
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise ValueError(f"{key} must be a mapping")
        for fallback_name, payload in value.items():
            definitions.append(
                _command_definition_from_config_entry(
                    fallback_name=str(fallback_name),
                    payload=payload,
                    source=source,
                )
            )
    return definitions


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

    rendered_template = _render_command_template_with_mask(
        definition.content,
        arguments,
    )
    rendered_content = rendered_template.text
    original_chars = len(rendered_content)
    truncated = original_chars > max_command_chars
    command_content = rendered_content[:max_command_chars]
    command_template_mask = rendered_template.template_mask[:max_command_chars]
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
        command_content=command_content,
        command_template_mask=command_template_mask,
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
        source="file",
    )


def _command_definition_from_skill(skill: SkillPackage) -> CommandDefinition:
    name = _normalize_command_name(skill.name)
    metadata = {
        "name": name,
        "source": "skill",
        "skill_name": skill.name,
        "skill_file": str(skill.skill_file),
        "skill_root": str(skill.root),
    }
    return CommandDefinition(
        name=name,
        description=skill.description,
        content=skill.content,
        command_file=skill.skill_file,
        metadata=metadata,
        source="skill",
    )


def _command_info_from_definition(definition: CommandDefinition) -> CommandInfo:
    return CommandInfo(
        name=definition.name,
        description=definition.description,
        source=definition.source,
        argument_hint=definition.argument_hint,
        agent=definition.agent,
        model=definition.model,
        subtask=deepcopy(definition.subtask),
        tools=_command_tools_for_listing(definition.metadata.get("tools")),
        hints=command_template_hints(definition.content),
        command_file=_command_file_for_listing(definition),
        metadata=deepcopy(definition.metadata),
    )


def _command_file_for_listing(definition: CommandDefinition) -> Path | None:
    if definition.source not in {"file", "skill"}:
        return None
    command_file = Path(definition.command_file)
    if str(command_file) == ".":
        return None
    return command_file


def _command_tools_for_listing(value: Any) -> list[str]:
    tools: list[str] = []
    seen: set[str] = set()

    def append(raw: Any) -> None:
        tool = str(raw).strip()
        if not tool or tool in seen:
            return
        seen.add(tool)
        tools.append(tool)

    if isinstance(value, str):
        for item in value.split(","):
            append(item)
        return tools

    if isinstance(value, (list, tuple, set)):
        for item in value:
            append(item)
        return tools

    return []


def _command_definition_from_config_entry(
    *,
    fallback_name: str,
    payload: Any,
    source: str,
) -> CommandDefinition:
    if isinstance(payload, str):
        name = _normalize_command_name(fallback_name)
        return CommandDefinition(
            name=name,
            content=payload,
            metadata={"name": name, "source": source},
            source=source,
        )

    if not isinstance(payload, Mapping):
        raise ValueError(
            f"Config command {fallback_name} must be a string or mapping"
        )

    raw_name = payload.get("name", fallback_name)
    name = _normalize_command_name(str(raw_name))
    template_key = _first_present_key(payload, CONFIG_TEMPLATE_KEYS)
    if template_key is None or payload.get(template_key) is None:
        raise ValueError(
            f"Config command {name or fallback_name} requires template or content"
        )

    metadata: dict[str, Any] = {}
    for key, value in payload.items():
        key_text = str(key)
        if key_text in CONFIG_TEMPLATE_KEYS:
            continue
        canonical_key = _canonical_metadata_key(key_text)
        metadata[canonical_key] = deepcopy(value)
    metadata["name"] = name
    metadata["source"] = source

    description = str(payload.get("description") or "")
    argument_hint = _first_present_value(
        metadata,
        ("argument-hint", "argument_hint", "argumentHint"),
    )
    return CommandDefinition(
        name=name,
        description=description,
        content=str(payload[template_key]),
        metadata=metadata,
        source=source,
        argument_hint=(
            None if argument_hint is None else str(argument_hint)
        ),
        agent=(
            None if payload.get("agent") is None else str(payload.get("agent"))
        ),
        model=(
            None if payload.get("model") is None else str(payload.get("model"))
        ),
        subtask=deepcopy(payload.get("subtask")) if "subtask" in payload else None,
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
        key = _canonical_metadata_key(stripped.split(":", 1)[0].strip())
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
        key = _canonical_metadata_key(key.strip())
        if key not in COMMAND_METADATA_KEYS:
            continue
        metadata[key] = _parse_metadata_value(key, value.strip())
    return metadata


def _parse_metadata_value(key: str, value: str) -> Any:
    if key == "tools":
        return _parse_tools_value(value)
    if key == "subtask":
        return _parse_subtask_value(value)
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


def _parse_subtask_value(value: str) -> Any:
    stripped = _strip_yaml_string(value.strip())
    lowered = stripped.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    return stripped


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
    ]
    command_file = _command_file_for_display(definition)
    if command_file:
        command_attrs.extend(
            [
                f'source="{escape(command_file, quote=True)}"',
                f'command_source="{escape(definition.source, quote=True)}"',
            ]
        )
    else:
        command_attrs.append(f'source="{escape(definition.source, quote=True)}"')
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


def find_command_shell_interpolations(
    command_content: str,
    *,
    template_mask: Iterable[bool] | None = None,
) -> list[CommandShellInterpolation]:
    """Return shell interpolation spans whose syntax came from the template."""

    if not command_content:
        return []

    mask = tuple(template_mask) if template_mask is not None else None
    interpolations: list[CommandShellInterpolation] = []
    offset = 0
    for line in command_content.splitlines(keepends=True):
        body_length = _line_body_length(line)
        body = line[:body_length]
        first = _first_non_space_index(body)
        if (
            first is not None
            and body[first] == "!"
            and _is_template_char(mask, offset + first)
        ):
            interpolation = _line_shell_interpolation(
                body,
                body_length=body_length,
                offset=offset,
                first=first,
                mask=mask,
                index=len(interpolations) + 1,
            )
            if interpolation is not None:
                interpolations.append(interpolation)
            offset += len(line)
            continue

        interpolations.extend(
            _inline_shell_interpolations(
                body,
                body_length=body_length,
                offset=offset,
                mask=mask,
                start_index=len(interpolations) + 1,
            )
        )
        offset += len(line)

    return interpolations


def apply_command_shell_execution_results(
    expansion: CommandExpansionResult,
    results: Iterable[CommandShellExecutionResult],
) -> CommandExpansionResult:
    """Return ``expansion`` with shell interpolation spans replaced by results."""

    ordered_results = sorted(results, key=lambda result: result.interpolation.start)
    if not ordered_results:
        return expansion

    parts: list[str] = []
    cursor = 0
    for result in ordered_results:
        interpolation = result.interpolation
        parts.append(expansion.command_content[cursor : interpolation.start])
        parts.append(_render_command_shell_result(result))
        cursor = interpolation.end
    parts.append(expansion.command_content[cursor:])
    command_content = "".join(parts)
    return replace(
        expansion,
        text=_render_expanded_command(
            definition=expansion.definition,
            command_content=command_content,
            arguments=expansion.arguments,
            remaining_text=expansion.remaining_text,
            truncated=expansion.truncated,
            original_chars=expansion.original_chars,
            max_chars=expansion.max_chars,
        ),
        command_content=command_content,
        command_template_mask=(),
        command_shell_interpolations=[
            result.to_metadata() for result in ordered_results
        ],
    )


def _render_command_shell_result(result: CommandShellExecutionResult) -> str:
    attrs = [
        f'index="{result.interpolation.index}"',
        f'status="{escape(result.status, quote=True)}"',
        f'success="{str(result.success).lower()}"',
        f'tool="{escape(result.tool_id, quote=True)}"',
        f'tool_call_id="{escape(result.tool_call_id, quote=True)}"',
        f'command="{escape(result.interpolation.command, quote=True)}"',
    ]
    return "\n".join(
        [
            f"<command_shell_result {' '.join(attrs)}>",
            result.content,
            "</command_shell_result>",
        ]
    )


def _line_shell_interpolation(
    line: str,
    *,
    body_length: int,
    offset: int,
    first: int,
    mask: tuple[bool, ...] | None,
    index: int,
) -> CommandShellInterpolation | None:
    if (
        first + 1 < body_length
        and line[first + 1] == "`"
        and _is_template_char(mask, offset + first + 1)
    ):
        closing = _find_template_backtick(
            line,
            start=first + 2,
            end=body_length,
            offset=offset,
            mask=mask,
        )
        if closing is not None:
            return CommandShellInterpolation(
                index=index,
                command=line[first + 2 : closing],
                start=offset + first,
                end=offset + closing + 1,
            )

    return CommandShellInterpolation(
        index=index,
        command=line[first + 1 : body_length].strip(),
        start=offset + first,
        end=offset + body_length,
    )


def _inline_shell_interpolations(
    line: str,
    *,
    body_length: int,
    offset: int,
    mask: tuple[bool, ...] | None,
    start_index: int,
) -> list[CommandShellInterpolation]:
    interpolations: list[CommandShellInterpolation] = []
    search_start = 0
    while search_start < body_length:
        start = line.find("!`", search_start, body_length)
        if start < 0:
            break
        if not (
            _is_template_char(mask, offset + start)
            and _is_template_char(mask, offset + start + 1)
        ):
            search_start = start + 2
            continue
        closing = _find_template_backtick(
            line,
            start=start + 2,
            end=body_length,
            offset=offset,
            mask=mask,
        )
        if closing is None:
            break
        interpolations.append(
            CommandShellInterpolation(
                index=start_index + len(interpolations),
                command=line[start + 2 : closing],
                start=offset + start,
                end=offset + closing + 1,
            )
        )
        search_start = closing + 1
    return interpolations


def _line_body_length(line: str) -> int:
    body_length = len(line)
    while body_length > 0 and line[body_length - 1] in "\r\n":
        body_length -= 1
    return body_length


def _first_non_space_index(text: str) -> int | None:
    for index, char in enumerate(text):
        if not char.isspace():
            return index
    return None


def _find_template_backtick(
    text: str,
    *,
    start: int,
    end: int,
    offset: int,
    mask: tuple[bool, ...] | None,
) -> int | None:
    cursor = text.find("`", start, end)
    while cursor >= 0:
        if _is_template_char(mask, offset + cursor):
            return cursor
        cursor = text.find("`", cursor + 1, end)
    return None


def _is_template_char(mask: tuple[bool, ...] | None, index: int) -> bool:
    if mask is None:
        return True
    return 0 <= index < len(mask) and mask[index]


def _render_command_template(content: str, arguments: str) -> str:
    return _render_command_template_with_mask(content, arguments).text


def _render_command_template_with_mask(
    content: str,
    arguments: str,
) -> _RenderedCommandTemplate:
    positional = _split_command_arguments(arguments)
    parts: list[str] = []
    mask: list[bool] = []
    cursor = 0

    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        if variable == "ARGUMENTS":
            return arguments
        index = int(variable) - 1
        if 0 <= index < len(positional):
            return positional[index]
        return ""

    for match in TEMPLATE_VARIABLE_RE.finditer(content):
        literal = content[cursor : match.start()]
        parts.append(literal)
        mask.extend([True] * len(literal))
        replacement = replace(match)
        parts.append(replacement)
        mask.extend([False] * len(replacement))
        cursor = match.end()
    literal = content[cursor:]
    parts.append(literal)
    mask.extend([True] * len(literal))
    return _RenderedCommandTemplate("".join(parts), tuple(mask))


def _split_command_arguments(arguments: str) -> list[str]:
    if not arguments:
        return []
    try:
        return shlex.split(arguments)
    except ValueError:
        return arguments.split()


def _command_file_for_display(definition: CommandDefinition) -> str:
    if definition.source not in {"file", "skill"}:
        return ""
    command_file = str(definition.command_file)
    return "" if command_file == "." else command_file


def _builtin_workspace_root_text(workspace_root: str | Path | None) -> str:
    if workspace_root is None:
        return "."
    return str(Path(workspace_root).expanduser().resolve(strict=False))


def _builtin_init_template(*, workspace_root: str, agents_path: str) -> str:
    return f"""Create or update the workspace agent guide.

Workspace root: {workspace_root}
Target file: {agents_path}
Additional user focus or constraints: $ARGUMENTS

Inspect the repository before editing. Capture high-signal facts future agents need:
development, test, build, lint, and formatting commands; architecture boundaries;
important entry points; generated or vendored paths to avoid; environment setup;
and project conventions that are easy to miss.

If {agents_path} already exists, preserve useful guidance and remove stale or
duplicated notes. Keep the final file concise, repository-specific, and
actionable."""


def _builtin_review_template() -> str:
    return """Review the requested code changes.

Review target: $ARGUMENTS

Choose the comparison source from the target:
- No target: inspect `git diff`, `git diff --cached`, and `git status --short`.
- Commit or revision: inspect `git show $ARGUMENTS`.
- Branch or ref: inspect `git diff $ARGUMENTS...HEAD`.
- Pull request number or URL: prefer `gh pr view $ARGUMENTS` and
  `gh pr diff $ARGUMENTS` when available.

Report findings first, ordered by severity. Focus on correctness, regressions,
security, data loss, missing tests, and risky behavior changes. Avoid generic
summaries unless there are no findings; then say that clearly and note any
residual test gaps."""


def _normalize_command_name(name: str) -> str:
    return str(name or "").strip().lstrip("/")


def _has_hidden_relative_part(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return any(part.startswith(".") for part in relative.parts)


def _metadata_string_value(
    explicit: Any,
    metadata: Mapping[str, Any],
    keys: Iterable[str],
) -> str | None:
    value = explicit
    if value is None:
        value = _first_present_value(metadata, keys)
    if value is None:
        return None
    return str(value)


def _first_present_key(mapping: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        if key in mapping:
            return key
    return None


def _first_present_value(mapping: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _canonical_metadata_key(key: str) -> str:
    if key in {"argument_hint", "argumentHint"}:
        return "argument-hint"
    return key


__all__ = [
    "CommandDefinition",
    "CommandExpansionResult",
    "CommandInfo",
    "CommandShellExecutionResult",
    "CommandShellInterpolation",
    "CommandRegistry",
    "apply_command_shell_execution_results",
    "builtin_command_definitions",
    "command_definitions_from_config",
    "command_template_hints",
    "discover_commands",
    "expand_command",
    "find_command_shell_interpolations",
]
