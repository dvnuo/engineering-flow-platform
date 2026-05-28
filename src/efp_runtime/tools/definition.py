"""Tool definition primitives for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..permissions import PermissionMetadata


class ValidationError(ValueError):
    """Raised when tool arguments do not match a tool schema."""


@dataclass(frozen=True)
class OutputPolicy:
    """Controls how tool output is serialized for model-visible context."""

    max_chars: int | None = None
    truncate: bool = True
    include_raw_output: bool = True


@dataclass(frozen=True)
class ToolContext:
    """Execution context passed to a tool implementation."""

    session_id: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str | None = None
    tool_name: str | None = None
    run_id: str | None = None
    iteration: int | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Return metadata with first-class execution context mirrored into it."""

        metadata = dict(self.metadata or {})
        _set_missing_metadata(metadata, "tool_call_id", self.tool_call_id)
        _set_missing_metadata(metadata, "tool_name", self.tool_name)
        _set_missing_metadata(metadata, "run_id", self.run_id)
        _set_missing_metadata(metadata, "iteration", self.iteration)
        return metadata


AsyncToolExecute = Callable[[dict[str, Any], ToolContext], Awaitable[Any]]


def _set_missing_metadata(metadata: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    existing = metadata.get(key)
    if existing is None or existing == "":
        metadata[key] = value


@dataclass(frozen=True)
class ToolDef:
    """A registered runtime v2 tool."""

    id: str
    description: str
    input_schema: Mapping[str, Any]
    execute: AsyncToolExecute
    permission: PermissionMetadata = field(default_factory=PermissionMetadata)
    output_policy: OutputPolicy = field(default_factory=OutputPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate_args(self, args: Mapping[str, Any] | None) -> dict[str, Any]:
        return validate_args(self.input_schema, args or {})


def validate_args(schema: Mapping[str, Any], args: Mapping[str, Any]) -> dict[str, Any]:
    """Validate args against a small JSON-schema-style object schema."""

    if not isinstance(args, Mapping):
        raise ValidationError("Tool arguments must be an object.")

    schema_type = schema.get("type")
    if schema_type not in (None, "object"):
        raise ValidationError("Tool input schema must be an object schema.")

    normalized = dict(args)
    _validate_object_properties(None, normalized, schema)
    return normalized


def _validate_object_properties(
    name: str | None,
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> None:
    properties = schema.get("properties", {})
    if properties is None:
        properties = {}
    if not isinstance(properties, Mapping):
        raise ValidationError("Tool input schema properties must be an object.")

    required = schema.get("required", [])
    if required is None:
        required = []
    if not isinstance(required, list):
        raise ValidationError("Tool input schema required field must be a list.")

    missing = [property_name for property_name in required if property_name not in value]
    if missing:
        if name is None:
            raise ValidationError(f"Missing required argument(s): {', '.join(missing)}")
        raise ValidationError(
            f"Argument '{name}' missing required property/properties: {', '.join(missing)}"
        )

    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            if name is None:
                raise ValidationError(f"Unexpected argument(s): {', '.join(extra)}")
            raise ValidationError(
                f"Argument '{name}' has unexpected property/properties: {', '.join(extra)}"
            )

    for property_name, property_value in value.items():
        property_schema = properties.get(property_name)
        if property_schema is None:
            additional_properties = schema.get("additionalProperties")
            if not isinstance(additional_properties, Mapping):
                continue
            property_schema = additional_properties
        _validate_value(_field_name(name, property_name), property_value, property_schema)


def _validate_value(name: str, value: Any, schema: Mapping[str, Any]) -> None:
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        allowed = ", ".join(repr(item) for item in enum)
        raise ValidationError(f"Argument '{name}' must be one of: {allowed}")

    expected = schema.get("type")
    if expected is None:
        return
    if isinstance(expected, list):
        if any(_matches_type(value, item) for item in expected):
            _validate_nested_value(name, value, schema)
            return
        expected_text = " or ".join(str(item) for item in expected)
        raise ValidationError(f"Argument '{name}' must be {expected_text}.")
    if not _matches_type(value, expected):
        raise ValidationError(f"Argument '{name}' must be {expected}.")
    _validate_nested_value(name, value, schema)


def _validate_nested_value(name: str, value: Any, schema: Mapping[str, Any]) -> None:
    if isinstance(value, Mapping):
        _validate_object_properties(name, value, schema)
        return

    if isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema is None:
            return
        if not isinstance(item_schema, Mapping):
            raise ValidationError(f"Argument '{name}' item schema must be an object.")
        for index, item in enumerate(value):
            _validate_value(f"{name}[{index}]", item, item_schema)


def _field_name(parent: str | None, child: str) -> str:
    if parent is None:
        return child
    return f"{parent}.{child}"


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True
