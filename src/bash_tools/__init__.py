"""Tools module - Shell execution via Linux CLI."""

from .api import (
    exec,
    exec_sync,
    get_tools_schemas,
    set_security_config,
    get_security_config,
    DEFAULT_SAFE_BINS,
)


def execute_tool(name: str, **kwargs) -> str:
    """Execute a tool by name."""
    if name == "exec":
        return exec_sync(kwargs.get("command", ""), kwargs.get("timeout", 60))
    return f"Error: Unknown tool: {name}"


__all__ = [
    "exec",
    "exec_sync",
    "get_tools_schemas",
    "execute_tool",
    "set_security_config",
    "get_security_config",
    "DEFAULT_SAFE_BINS",
]
