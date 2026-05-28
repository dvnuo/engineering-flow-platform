"""Skill package context messages for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable
from html import escape
from pathlib import Path
from typing import Any

from ..session.models import Message, MessagePart, MessageRole
from ..types import SkillPackage
from .discovery import SkillDiscovery


class SkillContextBuilder:
    """Build system messages that load discovered skills as model context."""

    def __init__(
        self,
        discovery: SkillDiscovery,
        *,
        include_sidecar_content: bool = False,
        max_sidecar_chars: int = 4000,
    ):
        self.discovery = discovery
        self.include_sidecar_content = include_sidecar_content
        self.max_sidecar_chars = max_sidecar_chars

    def build_messages(self, names: Iterable[str]) -> list[Message]:
        messages: list[Message] = []
        for name in names:
            skill_name = str(name)
            skill = self.discovery.get(skill_name)
            if skill is None:
                available = [item.name for item in self.discovery.discover()]
                raise KeyError(
                    f"Unknown skill: {skill_name}. Available skills: {', '.join(available)}"
                )
            messages.append(
                skill_package_to_system_message(
                    skill,
                    include_sidecar_content=self.include_sidecar_content,
                    max_sidecar_chars=self.max_sidecar_chars,
                )
            )
        return messages


def skill_package_to_system_message(
    skill: SkillPackage,
    *,
    include_sidecar_content: bool = False,
    max_sidecar_chars: int = 4000,
) -> Message:
    """Convert a discovered skill package into a Runtime v2 system message."""

    metadata = {
        "kind": "skill_context",
        "skill_name": skill.name,
        "skill_file": str(skill.skill_file),
    }
    return Message(
        role=MessageRole.SYSTEM,
        parts=[
            MessagePart.text_part(
                _render_skill_context_text(
                    skill,
                    include_sidecar_content=include_sidecar_content,
                    max_sidecar_chars=max_sidecar_chars,
                ),
                metadata=metadata,
            )
        ],
        metadata=metadata,
    )


def _render_skill_context_text(
    skill: SkillPackage,
    *,
    include_sidecar_content: bool,
    max_sidecar_chars: int,
) -> str:
    lines = [
        f'<skill_content name="{escape(skill.name, quote=True)}">',
        f"# Skill: {skill.name}",
    ]
    if skill.description:
        lines.append(f"Description: {skill.description}")
    lines.extend(
        [
            "",
            skill.content,
            "",
            f"Base directory for this skill: {_file_uri(skill.root)}",
            "<skill_files>",
        ]
    )
    lines.extend(
        _render_skill_file_lines(
            skill.root,
            skill.skill_file,
            include_content=False,
            max_chars=max_sidecar_chars,
        )
    )
    for path in skill.sidecar_files:
        lines.extend(
            _render_skill_file_lines(
                skill.root,
                path,
                include_content=include_sidecar_content,
                max_chars=max_sidecar_chars,
            )
        )
    lines.extend(
        [
            "</skill_files>",
            "</skill_content>",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _file_uri(path: Path) -> str:
    uri = path.resolve().as_uri()
    if not uri.endswith("/"):
        uri = f"{uri}/"
    return uri


def _render_skill_file_lines(
    root: Path,
    path: Path,
    *,
    include_content: bool,
    max_chars: int,
) -> list[str]:
    relative_path = _relative_sidecar_path(root, path)
    size = path.stat().st_size
    if not include_content:
        return [f"- {relative_path} ({size} bytes)"]

    sidecar = _read_sidecar_text(path, max_chars=max_chars)
    content_type = sidecar["content_type"]
    header = f"- {relative_path} ({size} bytes, {content_type})"
    if content_type != "text":
        return [header]

    if sidecar["truncated"]:
        header = (
            f"{header} truncated to {len(sidecar['content'])} of "
            f"{sidecar['original_chars']} chars"
        )
    return [header, "  Content:", _indent_text(str(sidecar["content"]), prefix="  ")]


def _render_sidecar_lines(
    root: Path,
    path: Path,
    *,
    include_content: bool,
    max_chars: int,
) -> list[str]:
    return _render_skill_file_lines(
        root,
        path,
        include_content=include_content,
        max_chars=max_chars,
    )


def _relative_sidecar_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read_sidecar_text(path: Path, *, max_chars: int) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"content": "", "content_type": "binary", "truncated": False}
    if "\x00" in content:
        return {"content": "", "content_type": "binary", "truncated": False}

    if max_chars >= 0 and len(content) > max_chars:
        return {
            "content": content[:max_chars],
            "content_type": "text",
            "truncated": True,
            "original_chars": len(content),
        }
    return {
        "content": content,
        "content_type": "text",
        "truncated": False,
        "original_chars": len(content),
    }


def _indent_text(text: str, *, prefix: str) -> str:
    if not text:
        return prefix
    return "\n".join(f"{prefix}{line}" for line in text.splitlines())
