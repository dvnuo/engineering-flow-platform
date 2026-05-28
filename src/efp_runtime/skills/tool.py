"""Skill context-loading tool for EFP Runtime v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..permissions import ALLOW, PermissionMetadata
from ..tools.definition import OutputPolicy, ToolContext, ToolDef
from ..types import SkillPackage
from .discovery import SkillDiscovery


class SkillTool:
    """Expose discovered skills as context, not executable Python code."""

    def __init__(
        self,
        discovery: SkillDiscovery,
        *,
        tool_id: str = "skill",
        max_sidecar_chars: int = 4000,
    ):
        self.discovery = discovery
        self.tool_id = tool_id
        self.max_sidecar_chars = max_sidecar_chars

    def definition(self) -> ToolDef:
        return ToolDef(
            id=self.tool_id,
            description="Load a discovered skill package as model context.",
            input_schema={
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "include_sidecar_content": {"type": "boolean"},
                    "max_sidecar_chars": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            execute=self.execute,
            permission=PermissionMetadata(action=ALLOW, category="context"),
            output_policy=OutputPolicy(max_chars=None),
        )

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        skill_name = str(args["name"])
        skill = self.discovery.get(skill_name)
        if skill is None:
            available = [item.name for item in self.discovery.discover()]
            raise KeyError(f"Unknown skill: {skill_name}. Available skills: {', '.join(available)}")

        include_sidecar_content = bool(args.get("include_sidecar_content", False))
        max_sidecar_chars = int(args.get("max_sidecar_chars") or self.max_sidecar_chars)
        return skill_package_to_context(
            skill,
            include_sidecar_content=include_sidecar_content,
            max_sidecar_chars=max_sidecar_chars,
        )


def build_skill_tool(
    directories: list[str | Path] | SkillDiscovery,
    *,
    tool_id: str = "skill",
    max_sidecar_chars: int = 4000,
) -> ToolDef:
    discovery = directories if isinstance(directories, SkillDiscovery) else SkillDiscovery(directories)
    return SkillTool(
        discovery,
        tool_id=tool_id,
        max_sidecar_chars=max_sidecar_chars,
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
        "metadata": dict(skill.metadata),
    }


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
