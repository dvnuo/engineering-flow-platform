"""Skill context-loading tool for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable
import codecs
from html import escape
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
    data={"subject_arg": "name"},
)

DEFAULT_SKILL_LIST_PERMISSION = PermissionMetadata(
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
        self.permission = _skill_permission(permission or DEFAULT_SKILL_PERMISSION)

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


class SkillListTool:
    """Expose discovered skill packages as a lightweight model-readable registry."""

    def __init__(
        self,
        discovery: SkillDiscovery,
        *,
        tool_id: str = "skill_list",
        permission: PermissionMetadata | None = None,
    ):
        self.discovery = discovery
        self.tool_id = tool_id
        self.permission = permission or DEFAULT_SKILL_LIST_PERMISSION

    def definition(self) -> ToolDef:
        return ToolDef(
            id=self.tool_id,
            description=(
                "List discovered skills, active skill state, and sidecar inventory "
                "without loading full skill context. Use skill to load one skill's "
                "complete <skill_content> when needed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "include_sidecars": {
                        "type": "boolean",
                        "description": (
                            "Whether to include sidecar file path, size, and "
                            "content type details. Defaults to true."
                        ),
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": (
                            "Whether to refresh skill discovery before listing."
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
        include_sidecars = bool(args.get("include_sidecars", True))
        refresh = bool(args.get("refresh", False))
        skills = self.discovery.discover(refresh=refresh)
        active_skills = _metadata_string_list(context.metadata.get("active_skills"))
        skill_entries = [
            skill_package_to_list_entry(skill, include_sidecars=include_sidecars)
            for skill in skills
        ]
        output = {
            "skills": skill_entries,
            "count": len(skill_entries),
            "active_skills": active_skills,
            "refresh": refresh,
        }
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name=context.tool_name or self.tool_id,
            status="success",
            success=True,
            content=_skill_list_to_content_text(
                skills,
                active_skills=active_skills,
            ),
            output=output,
            metadata={
                "count": len(skill_entries),
                "active_skills": list(active_skills),
                "active_skill_count": len(active_skills),
                "refresh": refresh,
            },
        )


def build_skill_tool(
    directories: Iterable[str | Path] | SkillDiscovery,
    *,
    tool_id: str = "skill",
    include_sidecar_content: bool = False,
    max_sidecar_chars: int = 4000,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    discovery = (
        directories
        if isinstance(directories, SkillDiscovery)
        else SkillDiscovery(directories)
    )
    return SkillTool(
        discovery,
        tool_id=tool_id,
        include_sidecar_content=include_sidecar_content,
        max_sidecar_chars=max_sidecar_chars,
        permission=permission,
    ).definition()


def build_skill_list_tool(
    directories: Iterable[str | Path] | SkillDiscovery,
    *,
    tool_id: str = "skill_list",
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    discovery = (
        directories
        if isinstance(directories, SkillDiscovery)
        else SkillDiscovery(directories)
    )
    return SkillListTool(
        discovery,
        tool_id=tool_id,
        permission=permission,
    ).definition()


def _skill_permission(permission: PermissionMetadata) -> PermissionMetadata:
    data = dict(permission.data)
    data.setdefault("subject_arg", "name")
    return PermissionMetadata(
        action=permission.action,
        reason=permission.reason,
        category=permission.category,
        resource=permission.resource,
        risk=permission.risk,
        data=data,
    )


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


def skill_package_to_list_entry(
    skill: SkillPackage,
    *,
    include_sidecars: bool = True,
) -> dict[str, Any]:
    sidecars = (
        [_sidecar_inventory_entry(skill.root, path) for path in skill.sidecar_files]
        if include_sidecars
        else []
    )
    return {
        "name": skill.name,
        "description": skill.description,
        "skill_file": str(skill.skill_file),
        "root": str(skill.root),
        "sidecar_count": len(skill.sidecar_files),
        "sidecars": sidecars,
        "metadata": dict(skill.metadata),
    }


def _skill_tool_description(discovery: SkillDiscovery) -> str:
    lines = [
        "Load a specialized skill by name: skill({name}) returns its full "
        "model-readable <skill_content> context.",
        "",
        "<available_skills>",
    ]
    skills = discovery.discover()
    if not skills:
        lines.append("  <no_skills>No skills available.</no_skills>")
        lines.append("</available_skills>")
        return "\n".join(lines)
    for skill in skills:
        lines.extend(
            [
                "  <skill>",
                f"    <name>{escape(skill.name)}</name>",
                f"    <description>{escape(skill.description or '')}</description>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def _skill_list_to_content_text(
    skills: Iterable[SkillPackage],
    *,
    active_skills: Iterable[str],
) -> str:
    lines = ["<available_skills>"]
    for skill in skills:
        description = skill.description or ""
        lines.append(
            f"- {skill.name}: {description} "
            f"({_sidecar_count_text(len(skill.sidecar_files))})"
        )
    lines.append("</available_skills>")

    active = [name for name in active_skills if str(name).strip()]
    lines.append("<active_skills>")
    for name in active:
        lines.append(f"- {name}")
    lines.append("</active_skills>")
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


def _sidecar_inventory_entry(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": _relative_sidecar_path(root, path),
        "size": path.stat().st_size,
        "content_type": _sidecar_content_type(path),
    }


def _sidecar_content_type(path: Path) -> str:
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                if b"\x00" in chunk:
                    return "binary"
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return "binary"
    return "text"


def _sidecar_count_text(count: int) -> str:
    suffix = "file" if count == 1 else "files"
    return f"{count} sidecar {suffix}"


def _relative_sidecar_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _metadata_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, Iterable):
        result: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if normalized:
                result.append(normalized)
        return result
    normalized = str(value).strip()
    return [normalized] if normalized else []


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
