"""Skill package context messages for EFP runtime."""

from __future__ import annotations

from collections.abc import Iterable
from html import escape
from pathlib import Path
from typing import Any

from ..session.models import Message, MessagePart, MessageRole
from ..types import SkillPackage
from .discovery import SkillDiscovery


DEFAULT_SKILL_FILE_SAMPLE_LIMIT = 10
RELATIVE_PATH_GUIDANCE = (
    "Relative paths in this skill (e.g., scripts/, reference/) are relative to "
    "this base directory."
)
SAMPLED_FILE_NOTE = "Note: file list is sampled."
AVAILABLE_SKILLS_GUIDANCE = (
    "Skills provide specialized instructions and workflows for specific tasks.",
    "Use the skill tool to load a skill when a task matches its description.",
)


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
    """Convert a discovered skill package into a EFP runtime system message."""

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


def available_skills_system_message(
    skills: Iterable[SkillPackage],
) -> Message | None:
    """Render the visible skill registry as transient provider-only context."""

    available_skills = sorted(
        [skill for skill in skills if str(skill.name).strip()],
        key=_skill_sort_key,
    )
    if not available_skills:
        return None

    metadata = {
        "kind": "available_skills",
        "source": "available_skills",
        "skill_count": len(available_skills),
    }
    return Message(
        role=MessageRole.SYSTEM,
        parts=[
            MessagePart.text_part(
                _render_available_skills_text(available_skills),
                metadata=metadata,
            )
        ],
        metadata=metadata,
        status="complete",
    )


def _render_available_skills_text(skills: Iterable[SkillPackage]) -> str:
    lines = [
        *AVAILABLE_SKILLS_GUIDANCE,
        "",
        "<available_skills>",
    ]
    for skill in skills:
        lines.extend(
            [
                "  <skill>",
                f"    <name>{escape(str(skill.name), quote=False)}</name>",
                "    <description>"
                f"{escape(str(skill.description or ''), quote=False)}"
                "</description>",
                "    <location>"
                f"{escape(str(skill.root.resolve()), quote=False)}"
                "</location>",
                "    <path>"
                f"{escape(str(skill.skill_file.resolve()), quote=False)}"
                "</path>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines).rstrip() + "\n"


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
            RELATIVE_PATH_GUIDANCE,
            SAMPLED_FILE_NOTE,
            "<skill_files>",
        ]
    )
    for path in _sample_sidecar_files(skill.sidecar_files):
        lines.extend(
            _render_skill_file_lines(
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


def _skill_sort_key(skill: SkillPackage) -> tuple[str, str]:
    return (str(skill.name).strip().lower(), str(skill.skill_file))


def _render_skill_file_lines(
    path: Path,
    *,
    include_content: bool,
    max_chars: int,
) -> list[str]:
    absolute_path = str(path.resolve())
    file_line = f"<file>{escape(absolute_path)}</file>"
    if not include_content:
        return [file_line]

    sidecar = _read_sidecar_text(path, max_chars=max_chars)
    content_type = sidecar["content_type"]
    content_attrs = [
        f'path="{escape(absolute_path, quote=True)}"',
        f'content_type="{content_type}"',
    ]
    if content_type != "text":
        return [file_line, f"<file_content {' '.join(content_attrs)} />"]

    content_attrs.append(f'truncated="{str(sidecar["truncated"]).lower()}"')
    content = str(sidecar["content"])
    content_lines = [file_line, f"<file_content {' '.join(content_attrs)}>"]
    if sidecar["truncated"]:
        content_lines.append(
            f"truncated to {len(content)} of {sidecar['original_chars']} chars"
        )
    content_lines.append(content)
    content_lines.append("</file_content>")
    return content_lines


def _sample_sidecar_files(
    sidecar_files: Iterable[Path],
    *,
    limit: int = DEFAULT_SKILL_FILE_SAMPLE_LIMIT,
) -> list[Path]:
    if limit < 0:
        return list(sidecar_files)
    return list(sidecar_files)[:limit]


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
