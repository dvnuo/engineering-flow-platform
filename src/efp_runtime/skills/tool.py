"""Skill context-loading tool for EFP runtime."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import escape
from pathlib import Path
from typing import Any

from ..permissions import (
    ALLOW,
    PermissionConfig,
    PermissionMetadata,
    is_permission_subject_hidden,
)
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
# Cap for a single skill file returned through ``skill(name, file=...)``.
DEFAULT_MAX_SKILL_FILE_CHARS = 60_000



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
        tool_permissions: Mapping[str, Any] | PermissionConfig | None = None,
    ):
        self.discovery = discovery
        self.tool_id = tool_id
        self.include_sidecar_content = include_sidecar_content
        self.max_sidecar_chars = max_sidecar_chars
        self.permission = _skill_permission(permission or DEFAULT_SKILL_PERMISSION)
        self.tool_permissions = _permission_config(tool_permissions)

    def definition(self) -> ToolDef:
        return ToolDef(
            id=self.tool_id,
            description=_skill_tool_description(
                self.discovery,
                tool_permissions=self.tool_permissions,
                tool_id=self.tool_id,
            ),
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
                    "file": {
                        "type": "string",
                        "description": (
                            "Path relative to the skill's base directory. When given, "
                            "the tool returns that file's text instead of the skill overview."
                        ),
                    },
                    "max_file_chars": {
                        "type": "integer",
                        "description": (
                            "Maximum characters to return for `file` "
                            f"(default {DEFAULT_MAX_SKILL_FILE_CHARS})."
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
        if skill is None or _skill_hidden(
            skill_name,
            tool_permissions=self.tool_permissions,
            tool_id=self.tool_id,
        ):
            available = _visible_skill_names(
                [item.name for item in self.discovery.discover()],
                tool_permissions=self.tool_permissions,
                tool_id=self.tool_id,
            )
            raise ValueError(
                f"Unknown skill: {skill_name}. "
                f"Available skills: {_available_skill_names_text(available)}"
            )

        requested_file = args.get("file")
        if isinstance(requested_file, str) and requested_file.strip():
            max_file_chars = int(args.get("max_file_chars") or DEFAULT_MAX_SKILL_FILE_CHARS)
            return _skill_file_result(
                skill,
                requested_file.strip(),
                max_chars=max_file_chars,
                context=context,
                tool_id=self.tool_id,
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


def _skill_file_result(
    skill: SkillPackage,
    requested: str,
    *,
    max_chars: int,
    context: ToolContext,
    tool_id: str,
) -> ToolResult:
    """Return one file from inside the skill directory, whatever the skill's layout."""

    root = skill.root.resolve()
    candidate = Path(requested)
    target = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=False)
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"File is outside the skill directory: {requested}") from exc
    if not target.is_file():
        raise FileNotFoundError(f"Skill file not found: {relative.as_posix()}")
    sidecar = _read_sidecar_text(target, max_chars=max_chars)
    if sidecar["content_type"] != "text":
        raise ValueError(
            f"Skill file is binary: {relative.as_posix()} ({target.stat().st_size} bytes). "
            "Use bash to process it."
        )
    truncated = bool(sidecar["truncated"])
    header = (
        f'<skill_file name="{escape(skill.name, quote=True)}" '
        f'path="{escape(str(target), quote=True)}" truncated="{str(truncated).lower()}">'
    )
    content = "\n".join([header, str(sidecar["content"]), "</skill_file>"])
    return ToolResult(
        call_id=context.tool_call_id or "",
        tool_name=context.tool_name or tool_id,
        content=content,
        output={
            "name": skill.name,
            "path": str(target),
            "relative_path": relative.as_posix(),
            "content": sidecar["content"],
            "truncated": truncated,
            "original_chars": sidecar.get("original_chars"),
        },
        metadata={
            "name": skill.name,
            "skill_file": str(skill.skill_file),
            "file": relative.as_posix(),
            "truncated": truncated,
        },
    )


def build_skill_tool(
    directories: Iterable[str | Path] | SkillDiscovery,
    *,
    tool_id: str = "skill",
    include_sidecar_content: bool = False,
    max_sidecar_chars: int = 4000,
    permission: PermissionMetadata | None = None,
    tool_permissions: Mapping[str, Any] | PermissionConfig | None = None,
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
        tool_permissions=tool_permissions,
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


def _permission_config(
    tool_permissions: Mapping[str, Any] | PermissionConfig | None,
) -> PermissionConfig | None:
    if tool_permissions is None:
        return None
    if isinstance(tool_permissions, PermissionConfig):
        return tool_permissions
    return PermissionConfig(tool_permissions)


def _visible_skills(
    skills: Iterable[SkillPackage],
    *,
    tool_permissions: PermissionConfig | None,
    tool_id: str,
) -> list[SkillPackage]:
    return [
        skill
        for skill in skills
        if not _skill_hidden(
            skill.name,
            tool_permissions=tool_permissions,
            tool_id=tool_id,
        )
    ]


def _visible_skill_names(
    names: Iterable[str],
    *,
    tool_permissions: PermissionConfig | None,
    tool_id: str,
) -> list[str]:
    return [
        name
        for name in names
        if not _skill_hidden(
            name,
            tool_permissions=tool_permissions,
            tool_id=tool_id,
        )
    ]


def _skill_hidden(
    skill_name: str,
    *,
    tool_permissions: PermissionConfig | None,
    tool_id: str,
) -> bool:
    return is_permission_subject_hidden(
        tool_permissions,
        tool_id=tool_id,
        category="skill",
        subject=skill_name,
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
            "path": path.relative_to(skill.root).as_posix(),
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


def _skill_tool_description(
    discovery: SkillDiscovery,
    *,
    tool_permissions: PermissionConfig | None,
    tool_id: str,
) -> str:
    lines = [
        "Load a specialized skill by name: skill({name}) returns its full "
        "model-readable <skill_content> context. skill({name, file}) returns one "
        "file from the skill's directory by relative path.",
        "",
        "<available_skills>",
    ]
    skills = _visible_skills(
        discovery.discover(),
        tool_permissions=tool_permissions,
        tool_id=tool_id,
    )
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
