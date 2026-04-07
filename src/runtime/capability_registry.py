"""Runtime capability registry for thin capability surface standardization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
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
            type=str(descriptor.type or "").strip(),
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
        normalized_type = str(capability_type or "").strip()
        if not normalized_type:
            return []
        return [item for item in self._capabilities.values() if item.type == normalized_type]

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

    def _register_skills(self) -> None:
        try:
            from src.skills.registry import skill_registry

            if not skill_registry.skills:
                skill_registry.load_skills()
            skills = skill_registry.list_active_skills()
            for skill in skills:
                descriptor = CapabilityDescriptor(
                    capability_id=f"skill:{skill.name}",
                    type="skill",
                    name=skill.name,
                    input_schema={"type": "object", "properties": {"session_id": {"type": "string"}, "input": {"type": "string"}}},
                    output_schema={"type": "object", "properties": {"status": {"type": "string"}, "output": {"type": "string"}}},
                    policy_tags=["skill", *( [skill.risk_level] if skill.risk_level else [] )],
                    requires_identity_binding=False,
                    enabled=not bool(skill.deprecated),
                    source_ref=skill.source_file or skill.path or None,
                    metadata={
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
                    capability_id="skill:default",
                    type="skill",
                    name="default",
                    policy_tags=["skill", "fallback"],
                    metadata={"fallback": True},
                )
            )

    def _register_adapter_actions(self) -> None:
        adapter_descriptors = [
            *build_github_adapter_capabilities(),
            *build_jira_adapter_capabilities(),
            *build_portal_adapter_capabilities(),
        ]
        for adapter_descriptor in adapter_descriptors:
            self.registry.register(
                CapabilityDescriptor(
                    capability_id=adapter_descriptor.action_id,
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
                        capability_id=f"channel_action:{item_name}",
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


def _dedupe_capability_id(capability_id: str) -> str:
    normalized = str(capability_id or "").strip().lower()
    return normalized or "capability:unknown"


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
