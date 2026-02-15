"""Tools module - Shell execution via Linux CLI."""

from .api import (
    exec,
    exec_sync,
    get_tools_schemas,
    set_security_config,
    get_security_config,
    DEFAULT_SAFE_BINS,
    # Legacy wrappers (for backward compat)
    read,
    write,
    edit,
    list_dir,
)


def execute_tool(name: str, **kwargs) -> str:
    """Execute a tool by name.
    
    All tools map to shell commands via exec.
    """
    if name == "exec":
        return exec_sync(kwargs.get("command", ""), kwargs.get("timeout", 60))
    
    # Legacy tool names map to shell commands
    if name == "read":
        return read(kwargs.get("file_path", ""), kwargs.get("limit"), kwargs.get("offset"))
    elif name == "write":
        return write(kwargs.get("file_path", ""), kwargs.get("content", ""))
    elif name == "edit":
        return edit(kwargs.get("file_path", ""), kwargs.get("oldText", ""), kwargs.get("newText", ""))
    elif name == "list_dir":
        return list_dir(kwargs.get("path", "."))
    
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
