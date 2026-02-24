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
        command = kwargs.get("command", "")
        args = kwargs.get("args")
        timeout = kwargs.get("timeout", 60)
        
        if args:
            return exec_sync(command, args, timeout)
        return exec_sync(command, timeout=timeout)
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
