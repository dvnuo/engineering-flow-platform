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
    "opencode_name",
    "mutation",
    "risk_level",
    "enabled",
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
    opencode_name: Optional[str] = None
    mutation: bool = False
    risk_level: Optional[str] = None
    enabled: bool = True


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


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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

    requires_identity_binding = _as_bool(data.get("requires_identity_binding"), default=False)
    enabled = _as_bool(data.get("enabled"), default=True)
    mutation = _as_bool(data.get("mutation"), default=False)

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
    if "opencode_name" in data:
        metadata.setdefault("opencode_name", _as_optional_string(data.get("opencode_name")))
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
        opencode_name=_as_optional_string(data.get("opencode_name")),
        mutation=mutation,
        risk_level=_as_optional_string(data.get("risk_level")),
        enabled=enabled,
    )


def descriptor_to_tool_schema(descriptor: ToolDescriptor) -> dict:
    raw_metadata = dict(descriptor.metadata or {})
    metadata = {
        **raw_metadata,
        "source": "external_tools_repo",
        "tool_id": descriptor.tool_id,
        "domain": descriptor.domain,
        "policy_tags": list(descriptor.policy_tags or []),
        "requires_identity_binding": descriptor.requires_identity_binding,
        "mutation": descriptor.mutation,
        "risk_level": descriptor.risk_level,
        "runtime_compat": list(descriptor.runtime_compat or []),
        "opencode_name": descriptor.opencode_name,
        "enabled": descriptor.enabled,
    }
    if "source" in raw_metadata and raw_metadata["source"] != "external_tools_repo":
        metadata["descriptor_source"] = raw_metadata["source"]
    return {
        "type": "function",
        "function": {
            "name": descriptor.name,
            "description": descriptor.description,
            "parameters": descriptor.input_schema or dict(DEFAULT_INPUT_SCHEMA),
        },
        "metadata": metadata,
    }


def is_descriptor_native_compatible(descriptor: ToolDescriptor) -> bool:
    compat = descriptor.runtime_compat or ["native"]
    return "native" in {item.lower() for item in compat}
