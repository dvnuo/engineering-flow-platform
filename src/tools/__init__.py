"""Tools module - File operations and shell execution tools."""

from .file_tools import (
    read,
    write,
    edit,
    list_dir,
    get_tools_schemas as get_file_tools_schemas,
)

from .exec_tools import (
    exec,
    exec_sync,
    get_tools_schemas as get_exec_tools_schemas,
)


def get_all_tools() -> list:
    """Get all tool schemas."""
    schemas = []
    schemas.extend(get_file_tools_schemas())
    schemas.extend(get_exec_tools_schemas())
    return schemas


def execute_tool(name: str, **kwargs) -> str:
    """Execute a tool by name (sync version for simple cases)."""
    if name == "read":
        return read(kwargs.get("file_path", ""), kwargs.get("limit"), kwargs.get("offset"))
    
    elif name == "write":
        return write(kwargs.get("file_path", ""), kwargs.get("content", ""))
    
    elif name == "edit":
        return edit(
            kwargs.get("file_path", ""),
            kwargs.get("oldText", ""),
            kwargs.get("newText", "")
        )
    
    elif name == "list_dir":
        return list_dir(kwargs.get("path", "."))
    
    elif name == "exec":
        return exec_sync(
            kwargs.get("command", ""),
            kwargs.get("timeout", 60),
            kwargs.get("workdir")
        )
    
    return f"Error: Unknown tool: {name}"


__all__ = [
    "read",
    "write", 
    "edit",
    "list_dir",
    "exec",
    "exec_sync",
    "get_all_tools",
    "get_file_tools_schemas",
    "get_exec_tools_schemas",
    "execute_tool",
]
