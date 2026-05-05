from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .contracts import ExternalToolExecutionResult, ToolDescriptor, descriptor_to_tool_schema, is_descriptor_native_compatible
from .manifest_loader import load_tool_descriptors, resolve_external_tools_dir
from .runner import execute_python_entrypoint

logger = logging.getLogger(__name__)

def _metadata_bool(value, default=False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class ExternalToolRegistry:
    def __init__(self, tools_dir: Optional[Path] = None, descriptors: Optional[list[ToolDescriptor]] = None):
        self.tools_dir = tools_dir or resolve_external_tools_dir()
        raw_descriptors = descriptors if descriptors is not None else load_tool_descriptors(self.tools_dir)

        self._descriptors_by_name: dict[str, ToolDescriptor] = {}
        for descriptor in raw_descriptors:
            if descriptor.name in self._descriptors_by_name:
                logger.warning("Duplicate external tool name '%s' skipped", descriptor.name)
                continue
            self._descriptors_by_name[descriptor.name] = descriptor

    def list_descriptors(self, *, runtime_type: str | None = None, enabled_only: bool = False, model_facing_only: bool = False) -> list[ToolDescriptor]:
        descriptors = list(self._descriptors_by_name.values())
        if runtime_type:
            target = runtime_type.strip().lower()
            descriptors = [
                d for d in descriptors
                if target in {item.lower() for item in (d.runtime_compat or ["native"])}
            ]
        if enabled_only:
            descriptors = [d for d in descriptors if d.enabled]
        if model_facing_only:
            descriptors = [d for d in descriptors if self.is_model_facing(d.name)]
        return descriptors

    def list_all_descriptors(self) -> list[ToolDescriptor]:
        return list(self._descriptors_by_name.values())

    def get_descriptor(self, name: str) -> ToolDescriptor | None:
        return self._descriptors_by_name.get(name)

    def get_tools_schema(self) -> list[dict]:
        return [
            descriptor_to_tool_schema(descriptor)
            for descriptor in self._descriptors_by_name.values()
            if is_descriptor_native_compatible(descriptor) and descriptor.enabled and self.is_model_facing(descriptor.name)
        ]

    def get_tool_names(self) -> list[str]:
        return [item.name for item in self.list_descriptors(runtime_type="native", enabled_only=True, model_facing_only=True)]

    def has_tool(self, name: str, *, include_disabled: bool = True) -> bool:
        descriptor = self._descriptors_by_name.get(name)
        if descriptor is None:
            return False
        return include_disabled or descriptor.enabled

    def is_tool_enabled(self, name: str) -> bool:
        descriptor = self.get_descriptor(name)
        return bool(descriptor and descriptor.enabled)

    def is_model_facing(self, name: str) -> bool:
        descriptor = self.get_descriptor(name)
        if not descriptor:
            return False
        return _metadata_bool(descriptor.metadata.get("model_facing"), default=True)

    def is_override_enabled(self, name: str) -> bool:
        descriptor = self.get_descriptor(name)
        if not descriptor:
            return False
        return bool(
            descriptor.enabled
            and is_descriptor_native_compatible(descriptor)
            and self.is_model_facing(name)
            and _metadata_bool(descriptor.metadata.get("allow_override"), default=False)
        )

    def get_visibility(self, name: str, *, legacy_names: set[str] | None = None) -> dict:
        legacy_names = legacy_names or set()
        descriptor = self.get_descriptor(name)
        legacy_name = name in legacy_names
        base = {
            "name": name,
            "exists": descriptor is not None,
            "enabled": False,
            "native_compatible": False,
            "model_facing": False,
            "legacy_name": legacy_name,
            "allow_override": False,
            "override_enabled": False,
            "exposed": False,
            "schema_source": "legacy_builtin" if legacy_name else "none",
            "execution_source": "legacy_builtin" if legacy_name else "none",
            "external_shadowed_by_legacy": False,
            "shadow_reason": None,
            "descriptor_source_file": None,
            "tool_id": None,
            "domain": None,
            "risk_level": None,
            "mutation": None,
            "opencode_name": None,
        }
        if descriptor is None:
            return base

        enabled = bool(descriptor.enabled)
        native_compatible = is_descriptor_native_compatible(descriptor)
        model_facing = self.is_model_facing(name)
        allow_override = _metadata_bool(descriptor.metadata.get("allow_override"), default=False)
        override_enabled = bool(enabled and native_compatible and model_facing and allow_override)
        exposed = bool(enabled and native_compatible and model_facing and (not legacy_name or override_enabled))

        if exposed:
            schema_source = "external_tools_repo"
            execution_source = "external_tools_repo"
            shadowed = False
            shadow_reason = None
        else:
            schema_source = "legacy_builtin" if legacy_name else "none"
            execution_source = "legacy_builtin" if legacy_name else "none"
            shadowed = bool(legacy_name and descriptor is not None)
            shadow_reason = None
            if not enabled:
                shadow_reason = "disabled"
            elif not native_compatible:
                shadow_reason = "runtime_incompatible"
            elif not model_facing:
                shadow_reason = "model_facing_false"
            elif legacy_name and not allow_override:
                shadow_reason = "allow_override_not_enabled"

        base.update(
            {
                "enabled": enabled,
                "native_compatible": native_compatible,
                "model_facing": model_facing,
                "allow_override": allow_override,
                "override_enabled": override_enabled,
                "exposed": exposed,
                "schema_source": schema_source,
                "execution_source": execution_source,
                "external_shadowed_by_legacy": shadowed,
                "shadow_reason": shadow_reason,
                "descriptor_source_file": descriptor.metadata.get("_source_file"),
                "tool_id": descriptor.tool_id,
                "domain": descriptor.domain,
                "risk_level": descriptor.risk_level,
                "mutation": descriptor.mutation,
                "opencode_name": descriptor.opencode_name,
            }
        )
        return base

    async def execute_tool(self, name: str, **kwargs) -> ExternalToolExecutionResult:
        descriptor = self.get_descriptor(name)
        if not descriptor:
            return ExternalToolExecutionResult(success=False, error=f"External tool '{name}' not found")
        if not descriptor.enabled:
            return ExternalToolExecutionResult(success=False, error=f"Tool '{name}' is disabled by external descriptor")
        return await execute_python_entrypoint(descriptor, self.tools_dir, **kwargs)


_registry_cache: ExternalToolRegistry | None = None


def get_external_tool_registry(force_reload: bool = False) -> ExternalToolRegistry:
    global _registry_cache
    if _registry_cache is None or force_reload:
        _registry_cache = ExternalToolRegistry()
    return _registry_cache


def reset_external_tool_registry_cache() -> None:
    global _registry_cache
    _registry_cache = None
