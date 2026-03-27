"""Simple test tools module."""

from .api import (
    test_echo,
    get_tools_schemas,
)


async def execute_tool(name: str, **kwargs) -> str:
    """Execute a tool by name (async)."""
    if name == "test_echo":
        result = await test_echo(**kwargs)
        return str(result)
    
    return f"Error: Unknown tool: {name}"


__all__ = [
    "test_echo",
    "get_tools_schemas",
    "execute_tool",
]
