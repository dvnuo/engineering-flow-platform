"""Deprecated: external tools subsystem removed from native runtime."""

from __future__ import annotations


def reset_external_tool_registry_cache() -> None:
    return None


def get_external_tool_registry(*args, **kwargs):
    class _RemovedRegistry:
        def get_tools_schema(self): return []
        def list_descriptors(self, *a, **k): return []
        def list_all_descriptors(self): return []
        def get_descriptor(self, name): return None
        def get_tool_names(self): return []
        def get_visibility(self, name, legacy_names=None):
            return {"exists": False, "enabled": False, "exposed": False, "name": name}
        async def execute_tool(self, name: str, **kwargs):
            return type("R", (), {"success": False, "content": "", "error": "External tools subsystem removed"})()
        def has_tool(self, name, include_disabled=True): return False
        def is_override_enabled(self, name): return False
    return _RemovedRegistry()
