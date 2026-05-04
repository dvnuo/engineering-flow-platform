from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .contracts import ExternalToolExecutionResult, ToolDescriptor, descriptor_to_tool_schema, is_descriptor_native_compatible
from .manifest_loader import load_tool_descriptors, resolve_external_tools_dir
from .runner import execute_python_entrypoint

logger = logging.getLogger(__name__)


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
        return descriptor.metadata.get("model_facing", True) is not False

    def is_override_enabled(self, name: str) -> bool:
        descriptor = self.get_descriptor(name)
        if not descriptor:
            return False
        return bool(
            descriptor.enabled
            and is_descriptor_native_compatible(descriptor)
            and self.is_model_facing(name)
            and descriptor.metadata.get("allow_override") is True
        )

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
