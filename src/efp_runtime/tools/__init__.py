"""Runtime v2 tool definitions, registry, and executor."""

from .definition import OutputPolicy, ToolContext, ToolDef, ValidationError
from .external import (
    ExternalToolContext,
    ExternalToolProvider,
    ExternalToolSpec,
    external_tool_defs,
    register_external_tools,
)
from .registry import ToolRegistry
from .runtime import ToolRuntime
from .selection import (
    ModelAwareToolSelection,
    ToolSelection,
    resolve_model_aware_tool_selection,
    resolve_tool_selection,
)

__all__ = [
    "ExternalToolContext",
    "ExternalToolProvider",
    "ExternalToolSpec",
    "OutputPolicy",
    "ToolContext",
    "ToolDef",
    "ToolRegistry",
    "ToolRuntime",
    "ToolSelection",
    "ValidationError",
    "ModelAwareToolSelection",
    "external_tool_defs",
    "register_external_tools",
    "resolve_model_aware_tool_selection",
    "resolve_tool_selection",
]
