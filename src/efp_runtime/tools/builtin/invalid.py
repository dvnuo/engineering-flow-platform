"""Invalid-arguments feedback tool for Runtime v2."""

from __future__ import annotations

from typing import Any

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


def create_invalid_tool() -> ToolDef:
    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        tool_name = args["tool"]
        error = args["error"]
        message = f"The arguments provided to the {tool_name} tool are invalid: {error}"
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name="invalid",
            content=message,
            output={
                "tool": tool_name,
                "error": error,
                "message": message,
            },
        )

    return ToolDef(
        id="invalid",
        description="Do not use.",
        input_schema={
            "type": "object",
            "required": ["tool", "error"],
            "properties": {
                "tool": {"type": "string"},
                "error": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="validation",
            resource="tool_arguments",
            risk="low",
        ),
    )
