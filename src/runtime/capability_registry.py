"""Runtime capability registry for thin capability surface standardization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime
import hashlib
import json
import logging

from src.runtime.capability_adapters import (
    build_github_adapter_capabilities,
    build_jira_adapter_capabilities,
    build_portal_adapter_capabilities,
)

logger = logging.getLogger(__name__)


@dataclass
class CapabilityDescriptor:
    capability_id: str
    type: str
    name: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    policy_tags: List[str] = field(default_factory=list)
    requires_identity_binding: bool = False
    enabled: bool = True
    source_ref: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class CapabilityRegistry:
    def register(self, descriptor: CapabilityDescriptor) -> None:
        raise NotImplementedError

    def get(self, capability_id: str) -> Optional[CapabilityDescriptor]:
        raise NotImplementedError

    def list_all(self) -> List[CapabilityDescriptor]:
        raise NotImplementedError

    def list_by_type(self, capability_type: str) -> List[CapabilityDescriptor]:
        raise NotImplementedError

    def list_enabled(self) -> List[CapabilityDescriptor]:
        raise NotImplementedError

    def exists(self, capability_id: str) -> bool:
        raise NotImplementedError

    def export_catalog(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def export_catalog_snapshot(self) -> Dict[str, Any]:
        raise NotImplementedError


class DefaultCapabilityRegistry(CapabilityRegistry):
    def __init__(self):
        self._capabilities: Dict[str, CapabilityDescriptor] = {}

    def register(self, descriptor: CapabilityDescriptor) -> None:
        capability_id = _dedupe_capability_id(descriptor.capability_id)
        if capability_id in self._capabilities:
            logger.debug("Capability already registered: %s", capability_id)
            return
        normalized = CapabilityDescriptor(
            capability_id=capability_id,
            type=_normalize_component(descriptor.type),
            name=str(descriptor.name or "").strip() or capability_id,
            input_schema=dict(descriptor.input_schema or {}),
            output_schema=dict(descriptor.output_schema or {}),
            policy_tags=list(descriptor.policy_tags or []),
            requires_identity_binding=bool(descriptor.requires_identity_binding),
            enabled=bool(descriptor.enabled),
            source_ref=descriptor.source_ref,
            metadata=dict(descriptor.metadata or {}),
        )
        self._capabilities[capability_id] = normalized

    def get(self, capability_id: str) -> Optional[CapabilityDescriptor]:
        return self._capabilities.get(_dedupe_capability_id(capability_id))

    def list_all(self) -> List[CapabilityDescriptor]:
        return list(self._capabilities.values())

    def list_by_type(self, capability_type: str) -> List[CapabilityDescriptor]:
        normalized_type = _normalize_component(capability_type)
        if not normalized_type:
            return []
        return [item for item in self._capabilities.values() if item.type == normalized_type]

    def list_enabled(self) -> List[CapabilityDescriptor]:
        return [item for item in self._capabilities.values() if item.enabled]

    def exists(self, capability_id: str) -> bool:
        return _dedupe_capability_id(capability_id) in self._capabilities

    def export_catalog(self) -> List[Dict[str, Any]]:
        return self.export_catalog_snapshot()["capabilities"]

    def export_catalog_snapshot(self) -> Dict[str, Any]:
        items = sorted(self._capabilities.values(), key=lambda item: item.capability_id)
        capabilities = [_descriptor_to_dict(item) for item in items]
        serialized = json.dumps(capabilities, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        catalog_version = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return {
            "capabilities": capabilities,
            "count": len(capabilities),
            "catalog_version": catalog_version,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    def register_many(self, descriptors: List[CapabilityDescriptor]) -> None:
        for descriptor in descriptors:
            self.register(descriptor)


class _CapabilityBuilder:
    def __init__(self, registry: DefaultCapabilityRegistry):
        self.registry = registry

    def populate_defaults(self) -> None:
        self._register_skills()
        self._register_adapter_actions()
        self._register_channel_actions()
        self._register_tools()

    def _register_skills(self) -> None:
        try:
            from src.skills.registry import skill_registry

            if not skill_registry.skills:
                skill_registry.load_skills()
            skills = skill_registry.list_active_skills()
            for skill in skills:
                descriptor = CapabilityDescriptor(
                    capability_id=_format_capability_id("skill", skill.name),
                    type="skill",
                    name=skill.name,
                    input_schema={"type": "object", "properties": {"session_id": {"type": "string"}, "input": {"type": "string"}}},
                    output_schema={"type": "object", "properties": {"status": {"type": "string"}, "output": {"type": "string"}}},
                    policy_tags=["skill", *( [skill.risk_level] if skill.risk_level else [] )],
                    requires_identity_binding=False,
                    enabled=not bool(skill.deprecated),
                    source_ref=skill.source_file or skill.path or None,
                    metadata={
                        "skill_name": skill.name,
                        "version": skill.version,
                        "owner": skill.owner,
                        "triggers": list(skill.triggers or []),
                        "tools": list(skill.tools or []),
                        "deprecated": bool(skill.deprecated),
                    },
                )
                self.registry.register(descriptor)
        except Exception:
            logger.debug("Failed to register skill capabilities", exc_info=True)

        if not self.registry.list_by_type("skill"):
            self.registry.register(
                CapabilityDescriptor(
                    capability_id=_format_capability_id("skill", "default"),
                    type="skill",
                    name="default",
                    policy_tags=["skill", "fallback"],
                    metadata={"fallback": True},
                )
            )

        self._register_required_runtime_skill_capabilities()


    def _register_required_runtime_skill_capabilities(self) -> None:
        required_descriptors = [
            CapabilityDescriptor(
                capability_id="skill:collect_requirements_to_bundle",
                type="skill",
                name="collect_requirements_to_bundle",
                policy_tags=["skill", "bundle"],
                requires_identity_binding=False,
                metadata={"runtime_required": True},
            ),
            CapabilityDescriptor(
                capability_id="skill:design_test_cases_from_bundle",
                type="skill",
                name="design_test_cases_from_bundle",
                policy_tags=["skill", "bundle"],
                requires_identity_binding=False,
                metadata={"runtime_required": True},
            ),
            CapabilityDescriptor(
                capability_id="skill:collect_research_notes_to_bundle",
                type="skill",
                name="collect_research_notes_to_bundle",
                policy_tags=["skill", "bundle"],
                requires_identity_binding=False,
                metadata={"runtime_required": True},
            ),
            CapabilityDescriptor(
                capability_id="skill:generate_implementation_plan_from_bundle",
                type="skill",
                name="generate_implementation_plan_from_bundle",
                policy_tags=["skill", "bundle"],
                requires_identity_binding=False,
                metadata={"runtime_required": True},
            ),
            CapabilityDescriptor(
                capability_id="skill:generate_runbook_from_bundle",
                type="skill",
                name="generate_runbook_from_bundle",
                policy_tags=["skill", "bundle"],
                requires_identity_binding=False,
                metadata={"runtime_required": True},
            ),
            CapabilityDescriptor(
                capability_id="skill:review-pull-request",
                type="skill",
                name="review-pull-request",
                policy_tags=["skill", "review", "github"],
                requires_identity_binding=True,
                metadata={"runtime_required": True, "provider": "github"},
            ),
        ]
        for descriptor in required_descriptors:
            if not self.registry.exists(descriptor.capability_id):
                self.registry.register(descriptor)

    def _register_adapter_actions(self) -> None:
        adapter_descriptors = [
            *build_github_adapter_capabilities(),
            *build_jira_adapter_capabilities(),
            *build_portal_adapter_capabilities(),
        ]
        for adapter_descriptor in adapter_descriptors:
            self.registry.register(
                CapabilityDescriptor(
                    capability_id=_normalize_adapter_action_id(adapter_descriptor.action_id),
                    type="adapter_action",
                    name=adapter_descriptor.name,
                    input_schema=adapter_descriptor.input_schema,
                    output_schema=adapter_descriptor.output_schema,
                    policy_tags=adapter_descriptor.policy_tags,
                    requires_identity_binding=adapter_descriptor.requires_identity_binding,
                    enabled=adapter_descriptor.enabled,
                    source_ref=adapter_descriptor.source_ref,
                    metadata={"adapter": adapter_descriptor.adapter, **adapter_descriptor.metadata},
                )
            )

    def _register_channel_actions(self) -> None:
        try:
            import src.channels as channels

            for item_name in getattr(channels, "__all__", []):
                if not item_name or not (item_name.startswith("jira_") or item_name.startswith("confluence_")):
                    continue
                item = getattr(channels, item_name, None)
                if not callable(item):
                    continue
                self.registry.register(
                    CapabilityDescriptor(
                        capability_id=_format_capability_id("channel_action", item_name),
                        type="channel_action",
                        name=item_name,
                        input_schema={"type": "object"},
                        output_schema={"type": "object"},
                        policy_tags=["channel_action"],
                        requires_identity_binding=True,
                        source_ref="src.channels",
                        metadata={"channel_action": item_name},
                    )
                )
        except Exception:
            logger.debug("Failed to register channel capabilities", exc_info=True)

    def _register_tools(self) -> None:
        try:
            from src import get_tools_schema

            for tool_schema in list(get_tools_schema() or []):
                if not isinstance(tool_schema, dict):
                    continue
                tool_name = _extract_tool_name(tool_schema)
                if not tool_name:
                    continue
                self.registry.register(
                    CapabilityDescriptor(
                        capability_id=_format_capability_id("tool", tool_name),
                        type="tool",
                        name=tool_name,
                        input_schema=dict(tool_schema.get("parameters") or {}),
                        output_schema={"type": "object"},
                        policy_tags=["tool", *(["read"] if _looks_read_only_tool(tool_name) else [])],
                        source_ref="src.__init__.get_tools_schema",
                        metadata={
                            "tool_name": tool_name,
                            "description": tool_schema.get("description"),
                            "declaration_only": True,
                        },
                    )
                )
        except Exception:
            logger.debug("Failed to register tool capabilities", exc_info=True)


def _dedupe_capability_id(capability_id: str) -> str:
    normalized = str(capability_id or "").strip().lower()
    parts = [_normalize_component(item) for item in normalized.split(":")]
    parts = [part for part in parts if part]
    if not parts:
        return "capability:unknown"
    if len(parts) == 1:
        return f"capability:{parts[0]}"
    return ":".join(parts)


def _normalize_component(value: Any) -> str:
    return "_".join(str(value or "").strip().lower().split())


def _format_capability_id(prefix: str, name: str) -> str:
    return f"{_normalize_component(prefix)}:{_normalize_component(name)}"


def _normalize_adapter_action_id(action_id: str) -> str:
    parts = [_normalize_component(item) for item in str(action_id or "").split(":")]
    if len(parts) >= 3 and parts[0] == "adapter":
        return f"adapter:{parts[1]}:{parts[2]}"
    return _dedupe_capability_id(action_id)


def _extract_tool_name(tool_schema: Dict[str, Any]) -> str:
    if isinstance(tool_schema.get("name"), str) and tool_schema.get("name").strip():
        return str(tool_schema.get("name")).strip()
    function_obj = tool_schema.get("function")
    if isinstance(function_obj, dict):
        function_name = function_obj.get("name")
        if isinstance(function_name, str) and function_name.strip():
            return function_name.strip()
    return ""


def _looks_read_only_tool(tool_name: str) -> bool:
    return any(token in tool_name.lower() for token in ("read", "get", "list"))


def _descriptor_to_dict(descriptor: CapabilityDescriptor) -> Dict[str, Any]:
    metadata = dict(descriptor.metadata or {})
    adapter_system = metadata.get("adapter_system") or metadata.get("adapter")
    action_alias = metadata.get("action_alias") or descriptor.name
    return {
        "capability_id": descriptor.capability_id,
        "type": descriptor.type,
        "name": descriptor.name,
        "logical_name": descriptor.name,
        "action_alias": action_alias if descriptor.type == "adapter_action" else None,
        "adapter_system": adapter_system if descriptor.type == "adapter_action" else None,
        "input_schema": dict(descriptor.input_schema or {}),
        "output_schema": dict(descriptor.output_schema or {}),
        "policy_tags": list(descriptor.policy_tags or []),
        "requires_identity_binding": bool(descriptor.requires_identity_binding),
        "enabled": bool(descriptor.enabled),
        "source_ref": descriptor.source_ref,
        "metadata": metadata,
    }


def build_default_capability_registry() -> CapabilityRegistry:
    registry = DefaultCapabilityRegistry()
    _CapabilityBuilder(registry).populate_defaults()
    return registry


_default_capability_registry: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    global _default_capability_registry
    if _default_capability_registry is None:
        _default_capability_registry = build_default_capability_registry()
    return _default_capability_registry


class _CapabilityRegistryProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(get_capability_registry(), name)


capability_registry = _CapabilityRegistryProxy()
