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
from .tools import get_all_tools as get_tools_all, execute_tool as execute_tools_func

# Also export raw functions for backward compatibility
from . import github
from . import jira
from . import confluence
from . import git
from . import tools


def get_all_tools() -> list:
    """Get all tool schemas."""
    tools = []
    tools.extend(get_github_tools())
    tools.extend(get_jira_tools())
    tools.extend(get_confluence_tools())
    tools.extend(get_git_tools())
    tools.extend(get_tools_all())
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
    import asyncio
    
    # Try file/exec tools first (imported from tools module)
    if name in ("read", "write", "edit", "list_dir", "exec"):
        if name in ("read", "write", "edit", "list_dir"):
            # Sync tools
            result = tools.execute_tool(name, **kwargs)
            return ToolResult(success="Error" not in result, content=result)
        else:
            # Exec tool (async)
            result = await tools.exec(
                kwargs.get("command", ""),
                kwargs.get("timeout", 60),
                kwargs.get("workdir")
            )
            return ToolResult(success="Error" not in result, content=result)
    
    # Git tools
    elif name in ("git_status", "git_commit", "git_push", "git_clone"):
        result = await _execute_git_tool(name, kwargs)
        return result
    
    # Jira tools
    elif name in ("jira_get_issue", "jira_search", "jira_add_comment"):
        result = await _execute_jira_tool(name, kwargs)
        return result
    
    # GitHub tools
    elif name in ("github_get_issue", "github_search_issues", "github_add_comment"):
        result = await _execute_github_tool(name, kwargs)
        return result
    
    # Confluence tools
    elif name in ("confluence_get_page", "confluence_search"):
        result = await _execute_confluence_tool(name, kwargs)
        return result
    
    return ToolResult(success=False, error=f"Tool {name} not implemented")


async def _execute_git_tool(name: str, kwargs: Dict) -> ToolResult:
    """Execute a git tool."""
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
    
    return ToolResult(success=False, error=f"Unknown git tool: {name}")


async def _execute_jira_tool(name: str, kwargs: Dict) -> ToolResult:
    """Execute a jira tool."""
    from . import jira as jira_module
    
    if name == "jira_get_issue":
        issue_key = kwargs.get("issue_key", "")
        result = await jira_module.jira_get_issue(issue_key)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_search":
        jql = kwargs.get("jql", "")
        max_results = kwargs.get("max_results", 10)
        result = await jira_module.jira_search(jql, max_results)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_add_comment":
        issue_key = kwargs.get("issue_key", "")
        comment = kwargs.get("comment", "")
        result = await jira_module.jira_add_comment(issue_key, comment)
        return ToolResult(success="Error" not in result, content=result)
    
    return ToolResult(success=False, error=f"Unknown jira tool: {name}")


async def _execute_github_tool(name: str, kwargs: Dict) -> ToolResult:
    """Execute a github tool."""
    from . import github as github_module
    
    if name == "github_get_issue":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        issue_number = kwargs.get("issue_number", 0)
        result = await github_module.github_get_issue(owner, repo, issue_number)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "github_search_issues":
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 10)
        result = await github_module.github_search_issues(query, max_results)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "github_add_comment":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        issue_number = kwargs.get("issue_number", 0)
        comment = kwargs.get("comment", "")
        result = await github_module.github_add_comment(owner, repo, issue_number, comment)
        return ToolResult(success="Error" not in result, content=result)
    
    return ToolResult(success=False, error=f"Unknown github tool: {name}")


async def _execute_confluence_tool(name: str, kwargs: Dict) -> ToolResult:
    """Execute a confluence tool."""
    from . import confluence as confluence_module
    
    if name == "confluence_get_page":
        page_id = kwargs.get("page_id", "")
        result = await confluence_module.confluence_get_page(page_id)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_search":
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 10)
        result = await confluence_module.confluence_search(query, max_results)
        return ToolResult(success="Error" not in result, content=result)
    
    return ToolResult(success=False, error=f"Unknown confluence tool: {name}")


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
    "tools",
]
