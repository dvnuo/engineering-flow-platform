"""Per-run structured output terminal tool for EFP runtime."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


DEFAULT_STRUCTURED_OUTPUT_TOOL_ID = "StructuredOutput"


def create_structured_output_tool(
    output_schema: Mapping[str, Any],
    *,
    tool_id: str = DEFAULT_STRUCTURED_OUTPUT_TOOL_ID,
) -> ToolDef:
    input_schema = _normalize_output_schema(output_schema)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        structured_output = deepcopy(dict(args))
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name=tool_id,
            status="success",
            success=True,
            content="Structured output captured successfully.",
            output=deepcopy(structured_output),
            metadata={
                "terminal": True,
                "terminal_reason": "structured_output",
                "structured_output": deepcopy(structured_output),
                "valid": True,
            },
        )

    return ToolDef(
        id=tool_id,
        description="Submit the final structured output object and end the run.",
        input_schema=input_schema,
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="structured_output",
            resource="response",
            risk="low",
        ),
    )


def _normalize_output_schema(output_schema: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(output_schema, Mapping):
        raise ValueError("Structured output schema must be an object schema.")

    schema = deepcopy(dict(output_schema))
    schema.pop("$schema", None)

    schema_type = schema.get("type")
    if schema_type is None:
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            raise ValueError(
                "Structured output schema without a type must define object properties."
            )
        schema["type"] = "object"
        return schema

    if schema_type != "object":
        raise ValueError("Structured output schema must have type 'object'.")

    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, Mapping):
        raise ValueError("Structured output schema properties must be an object.")
    return schema


__all__ = [
    "DEFAULT_STRUCTURED_OUTPUT_TOOL_ID",
    "create_structured_output_tool",
]
