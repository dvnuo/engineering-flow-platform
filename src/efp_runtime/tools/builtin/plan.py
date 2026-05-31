"""Plan-mode terminal tool for EFP runtime."""

from __future__ import annotations

from typing import Any

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


def create_plan_exit_tool() -> ToolDef:
    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        plan = _optional_string(args.get("plan"))
        status = str(args.get("status") or "ready")
        summary = _optional_string(args.get("summary"))
        next_steps = _string_list(args.get("next_steps"))
        risks = _string_list(args.get("risks"))
        output = {
            "plan": plan,
            "status": status,
            "summary": summary,
            "next_steps": next_steps,
            "risks": risks,
        }
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name="plan_exit",
            status="success",
            success=True,
            content=_render_plan_content(output),
            output=output,
            metadata={
                "terminal": True,
                "terminal_reason": "plan_exit",
                "plan_status": status,
            },
        )

    return ToolDef(
        id="plan_exit",
        description="Submit the final plan and end the current runtime loop.",
        input_schema={
            "type": "object",
            "required": [],
            "properties": {
                "plan": {"type": "string"},
                "status": {"type": "string"},
                "next_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "risks": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "summary": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="plan",
            resource="session",
            risk="low",
        ),
    )


def _optional_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return [str(value)]
    return [str(item) for item in value]


def _render_plan_content(output: dict[str, Any]) -> str:
    lines = [f"Plan submitted with status: {output['status']}."]
    if output["summary"]:
        lines.extend(["", "Summary:", str(output["summary"])])
    lines.extend(["", "Plan:", str(output["plan"])])
    if output["next_steps"]:
        lines.append("")
        lines.append("Next steps:")
        lines.extend(f"- {step}" for step in output["next_steps"])
    if output["risks"]:
        lines.append("")
        lines.append("Risks:")
        lines.extend(f"- {risk}" for risk in output["risks"])
    return "\n".join(lines)


__all__ = ["create_plan_exit_tool"]
