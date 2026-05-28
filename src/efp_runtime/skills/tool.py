"""Skill context-loading tool for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..permissions import ALLOW, PermissionMetadata
from ..tools.definition import OutputPolicy, ToolContext, ToolDef
from ..types import SkillPackage, ToolResult
from .context import skill_package_to_system_message
from .discovery import SkillDiscovery


DEFAULT_SKILL_PERMISSION = PermissionMetadata(
    action=ALLOW,
    category="skill",
    resource="context",
    risk="low",
)


class SkillTool:
    """Expose discovered skills as context, not executable Python code."""

    def __init__(
        self,
        discovery: SkillDiscovery,
        *,
        tool_id: str = "skill",
        include_sidecar_content: bool = False,
        max_sidecar_chars: int = 4000,
        permission: PermissionMetadata | None = None,
    ):
        self.discovery = discovery
        self.tool_id = tool_id
        self.include_sidecar_content = include_sidecar_content
        self.max_sidecar_chars = max_sidecar_chars
        self.permission = permission or DEFAULT_SKILL_PERMISSION

    def definition(self) -> ToolDef:
        return ToolDef(
            id=self.tool_id,
            description=_skill_tool_description(self.discovery),
            input_schema={
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the skill to load.",
                    },
                    "include_sidecar_content": {
                        "type": "boolean",
                        "description": (
                            "Whether to include text sidecar file contents in the "
                            "returned skill context."
                        ),
                    },
                    "max_sidecar_chars": {
                        "type": "integer",
                        "description": (
                            "Maximum characters to include from each text sidecar "
                            "when include_sidecar_content is true."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            execute=self.execute,
            permission=self.permission,
            output_policy=OutputPolicy(max_chars=None),
        )

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        skill_name = str(args["name"])
        skill = self.discovery.get(skill_name)
        if skill is None:
            available = [item.name for item in self.discovery.discover()]
            raise ValueError(
                f"Unknown skill: {skill_name}. "
                f"Available skills: {_available_skill_names_text(available)}"
            )

        include_sidecar_content = bool(
            args.get("include_sidecar_content", self.include_sidecar_content)
        )
        max_sidecar_chars = int(args.get("max_sidecar_chars") or self.max_sidecar_chars)
        output = skill_package_to_context(
            skill,
            include_sidecar_content=include_sidecar_content,
            max_sidecar_chars=max_sidecar_chars,
        )
        metadata = _skill_result_metadata(skill, sidecar_count=len(output["sidecars"]))
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name=context.tool_name or self.tool_id,
            content=_skill_package_to_content_text(
                skill,
                include_sidecar_content=include_sidecar_content,
                max_sidecar_chars=max_sidecar_chars,
            ),
            output=output,
            metadata=metadata,
        )


def build_skill_tool(
    directories: Iterable[str | Path] | SkillDiscovery,
    *,
    tool_id: str = "skill",
    include_sidecar_content: bool = False,
    max_sidecar_chars: int = 4000,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    discovery = directories if isinstance(directories, SkillDiscovery) else SkillDiscovery(directories)
    return SkillTool(
        discovery,
        tool_id=tool_id,
        include_sidecar_content=include_sidecar_content,
        max_sidecar_chars=max_sidecar_chars,
        permission=permission,
    ).definition()


def skill_package_to_context(
    skill: SkillPackage,
    *,
    include_sidecar_content: bool = False,
    max_sidecar_chars: int = 4000,
) -> dict[str, Any]:
    sidecars = []
    for path in skill.sidecar_files:
        entry: dict[str, Any] = {
            "path": str(path.relative_to(skill.root)),
            "size": path.stat().st_size,
        }
        if include_sidecar_content:
            entry.update(_read_sidecar_text(path, max_chars=max_sidecar_chars))
        sidecars.append(entry)

    return {
        "name": skill.name,
        "description": skill.description,
        "skill_file": str(skill.skill_file),
        "content": skill.content,
        "sidecars": sidecars,
        "metadata": {
            **dict(skill.metadata),
            **_skill_result_metadata(skill, sidecar_count=len(sidecars)),
        },
    }


def _skill_tool_description(discovery: SkillDiscovery) -> str:
    lines = [
        "Load a specialized skill by name and return its full model-readable "
        "<skill_content> context. Use this when the task would benefit from "
        "instructions or references from a discovered skill package.",
        "",
        "Available skills:",
    ]
    skills = discovery.discover()
    if not skills:
        lines.append("No skills available.")
        return "\n".join(lines)
    for skill in skills:
        if skill.description:
            lines.append(f"- {skill.name}: {skill.description}")
        else:
            lines.append(f"- {skill.name}")
    return "\n".join(lines)


def _skill_package_to_content_text(
    skill: SkillPackage,
    *,
    include_sidecar_content: bool,
    max_sidecar_chars: int,
) -> str:
    message = skill_package_to_system_message(
        skill,
        include_sidecar_content=include_sidecar_content,
        max_sidecar_chars=max_sidecar_chars,
    )
    if not message.parts:
        return ""
    return message.parts[0].text or ""


def _skill_result_metadata(
    skill: SkillPackage,
    *,
    sidecar_count: int,
) -> dict[str, Any]:
    return {
        "name": skill.name,
        "skill_file": str(skill.skill_file),
        "sidecar_count": sidecar_count,
    }


def _available_skill_names_text(available: Iterable[str]) -> str:
    names = [str(name) for name in available]
    if not names:
        return "none"
    return ", ".join(names)


def _read_sidecar_text(path: Path, *, max_chars: int) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
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
