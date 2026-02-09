"""Tools module - File operations and shell execution tools.

遵循项目命名规范:
- api.py 包含所有工具实现
- __init__.py 导出工具函数
"""

from .api import (
    read,
    write,
    edit,
    list_dir,
    exec,
    exec_sync,
    get_tools_schemas,
)


def get_all_tools() -> list:
    """Get all tool schemas."""
    return get_tools_schemas()


def execute_tool(name: str, **kwargs) -> str:
    """Execute a tool by name (sync version)."""
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
    "get_tools_schemas",
    "execute_tool",
]
