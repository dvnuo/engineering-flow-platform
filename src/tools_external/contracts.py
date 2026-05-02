from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_INPUT_SCHEMA = {"type": "object", "properties": {}}
KNOWN_DESCRIPTOR_KEYS = {
    "tool_id",
    "name",
    "description",
    "input_schema",
    "output_schema",
    "domain",
    "type",
    "policy_tags",
    "runtime_compat",
    "python_entrypoint",
    "metadata",
    "requires_identity_binding",
}


@dataclass(frozen=True)
class ToolDescriptor:
    tool_id: str
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Optional[Dict[str, Any]] = None
    domain: Optional[str] = None
    type: str = "tool"
    policy_tags: List[str] = field(default_factory=list)
    runtime_compat: List[str] = field(default_factory=list)
    requires_identity_binding: bool = False
    python_entrypoint: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalToolExecutionResult:
    success: bool
    content: str = ""
    error: Optional[str] = None


def _as_string_list(value: Any, default: Optional[List[str]] = None) -> List[str]:
    if isinstance(value, list):
        result = [str(item).strip() for item in value if str(item).strip()]
        if result:
            return result
    if default is None:
        return []
    return list(default)


def _as_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def descriptor_from_mapping(data: dict, source_file: str | None = None) -> ToolDescriptor:
    if not isinstance(data, dict):
        raise ValueError("tool descriptor must be a mapping")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tool descriptor must include a non-empty string 'name'")
    name = name.strip()

    tool_id = data.get("tool_id")
    if isinstance(tool_id, str) and tool_id.strip():
        tool_id = tool_id.strip()
    else:
        tool_id = f"tool:{name}"

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        description = name
    else:
        description = description.strip()

    input_schema = data.get("input_schema")
    if not isinstance(input_schema, dict):
        input_schema = dict(DEFAULT_INPUT_SCHEMA)

    output_schema = data.get("output_schema")
    if not isinstance(output_schema, dict):
        output_schema = None

    requires_identity_binding = bool(data.get("requires_identity_binding", False))

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    else:
        metadata = dict(metadata)

    for key, value in data.items():
        if key not in KNOWN_DESCRIPTOR_KEYS and key not in metadata:
            metadata[key] = value
    if "requires_identity_binding" in data:
        metadata.setdefault("requires_identity_binding", requires_identity_binding)
    if source_file:
        metadata["_source_file"] = source_file

    runtime_compat = _as_string_list(data.get("runtime_compat"), default=["native"])
    policy_tags = _as_string_list(data.get("policy_tags"))

    return ToolDescriptor(
        tool_id=tool_id,
        name=name,
        description=description,
        input_schema=dict(input_schema or DEFAULT_INPUT_SCHEMA),
        output_schema=dict(output_schema) if isinstance(output_schema, dict) else None,
        domain=_as_optional_string(data.get("domain")),
        type=_as_optional_string(data.get("type")) or "tool",
        policy_tags=policy_tags,
        runtime_compat=runtime_compat,
        requires_identity_binding=requires_identity_binding,
        python_entrypoint=_as_optional_string(data.get("python_entrypoint")),
        metadata=metadata,
    )


def descriptor_to_tool_schema(descriptor: ToolDescriptor) -> dict:
    return {
        "type": "function",
        "function": {
            "name": descriptor.name,
            "description": descriptor.description,
            "parameters": descriptor.input_schema or dict(DEFAULT_INPUT_SCHEMA),
        },
    }


def is_descriptor_native_compatible(descriptor: ToolDescriptor) -> bool:
    compat = descriptor.runtime_compat or ["native"]
    return "native" in {item.lower() for item in compat}
