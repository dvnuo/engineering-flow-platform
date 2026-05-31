"""Skill command parsing for EFP runtime."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SkillCommandResult:
    """Parsed skill command directives plus the remaining user text."""

    cleaned_text: str
    add: list[str] = field(default_factory=list)
    clear: bool = False


@dataclass(frozen=True)
class SkillSlashCommandLine:
    """A parsed fallback slash command line for skill activation."""

    name: str
    arguments: str
    cleaned_text: str


def parse_skill_commands(text: str) -> SkillCommandResult:
    """Parse `/skill ...` command lines without mutating runtime state."""

    add: list[str] = []
    clear = False
    kept_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        command = _parse_skill_command(line)
        if command == "clear":
            clear = True
            continue
        if command:
            add.append(command)
            continue
        kept_lines.append(line)

    return SkillCommandResult(cleaned_text="".join(kept_lines), add=add, clear=clear)


def parse_skill_slash_command_line(text: str) -> SkillSlashCommandLine | None:
    """Parse the first effective slash command line for skill fallback."""

    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("/"):
            return None

        command_text = stripped[1:]
        if not command_text or command_text[0].isspace():
            return None

        parts = command_text.split(None, 1)
        name = parts[0]
        arguments = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            return None

        remaining_text = "".join(lines[index + 1 :])
        cleaned_text = _combine_slash_arguments_and_body(arguments, remaining_text)
        return SkillSlashCommandLine(
            name=name,
            arguments=arguments,
            cleaned_text=cleaned_text,
        )
    return None


def _parse_skill_command(line: str) -> str | None:
    stripped = line.strip()
    if stripped == "/skill":
        return None
    if not stripped.startswith("/skill"):
        return None
    if len(stripped) <= len("/skill") or not stripped[len("/skill")].isspace():
        return None

    argument = stripped[len("/skill") :].strip()
    if not argument:
        return None
    return argument


def _combine_slash_arguments_and_body(arguments: str, remaining_text: str) -> str:
    if not arguments:
        return remaining_text
    if not remaining_text:
        return arguments
    return f"{arguments}\n{remaining_text}"
