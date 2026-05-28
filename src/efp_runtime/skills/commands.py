"""Skill command parsing for EFP Runtime v2."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SkillCommandResult:
    """Parsed skill command directives plus the remaining user text."""

    cleaned_text: str
    add: list[str] = field(default_factory=list)
    clear: bool = False


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
