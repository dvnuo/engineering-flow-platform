"""
Tools - Unified tool exports for Engineering Flow Platform.

This module exports all tools from src/tools/* for agent use.
"""

from typing import Any, Dict, List, Optional


class ToolResult:
    """Result from tool execution."""

    def __init__(
        self,
        success: bool,
        content: str = "",
        error: Optional[str] = None,
    ):
        self.success = success
        self.content = content
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "error": self.error,
        }

    def __str__(self) -> str:
        if self.success:
            return self.content
        return f"Error: {self.error}"


class Tool:
    """Base class for tools."""

    name: str = "base_tool"
    description: str = "A tool"

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool."""
        return ToolResult(success=False, error="Not implemented")


# Global tools registry
TOOLS: Dict[str, Tool] = {}


from .github import get_tools_schemas as get_github_tools
from .jira import get_tools_schemas as get_jira_tools
from .confluence import get_tools_schemas as get_confluence_tools
from .git import get_tools_schemas as get_git_tools

# Also export raw functions for backward compatibility
from . import github
from . import jira
from . import confluence
from . import git


def get_all_tools() -> list:
    """Get all tool schemas."""
    tools = []
    tools.extend(get_github_tools())
    tools.extend(get_jira_tools())
    tools.extend(get_confluence_tools())
    tools.extend(get_git_tools())
    return tools


def get_tool_names() -> List[str]:
    """Get all tool names."""
    return [t["name"] for t in get_all_tools()]


def get_tool(name: str) -> Optional[Dict]:
    """Get tool schema by name."""
    for tool in get_all_tools():
        if tool.get("name") == name:
            return tool
    return None


def get_tools_schema() -> List[Dict]:
    """Get all tool schemas for LLM context."""
    return get_all_tools()


async def execute_tool(name: str, **kwargs) -> ToolResult:
    """Execute a tool by name."""
    # Simple implementation - can be extended
    return ToolResult(success=False, error=f"Tool {name} not implemented")


__all__ = [
    "get_all_tools",
    "get_tool_names",
    "get_tool",
    "get_tools_schema",
    "execute_tool",
    "get_github_tools",
    "get_jira_tools",
    "get_confluence_tools",
    "get_git_tools",
    "github_api",
    "jira",
    "confluence",
    "git_api",
    "ToolResult",
    "Tool",
    "TOOLS",
]


# Add git_clone to execute_tool
async def execute_tool(name: str, **kwargs) -> ToolResult:
    """Execute a tool by name."""
    from . import git as git_module
    
    if name == "git_status":
        workspace = kwargs.get("workspace", ".")
        result = await git_module.git_status(workspace)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "git_commit":
        message = kwargs.get("message", "")
        workspace = kwargs.get("workspace", ".")
        result = await git_module.git_commit(message, workspace)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "git_push":
        workspace = kwargs.get("workspace", ".")
        result = await git_module.git_push(workspace)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "git_clone":
        repo_url = kwargs.get("repo_url", "")
        workspace = kwargs.get("workspace", ".")
        result = await git_module.git_clone(repo_url, workspace)
        return ToolResult(success="Error" not in result, content=result)
    
    return ToolResult(success=False, error=f"Tool {name} not implemented")
