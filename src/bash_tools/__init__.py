"""Tools module - Shell execution via Linux CLI."""

from .api import (
    discover_commands,
    run_command,
    get_tools_schemas,
    get_workspace_dir,
)


async def execute_tool(name: str, **kwargs) -> str:
    """Execute a tool by name (async)."""
    if name == "discover_commands":
        result = await discover_commands(**kwargs)
        return str(result)
    
    if name == "run_command":
        result = await run_command(**kwargs)
        return str(result)
    
    return f"Error: Unknown tool: {name}"


__all__ = [
    "discover_commands",
    "run_command",
    "get_tools_schemas",
    "execute_tool",
    "get_workspace_dir",
]
