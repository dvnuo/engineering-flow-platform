"""
Bash Tools - Shell execution and file operations.

Bash tools with security controls.
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
            return self.content if self.content else "(no result)"
        # Error case: prefer error field, fallback to content
        if self.error:
            return f"Error: {self.error}"
        return self.content if self.content else "Error: Unknown (no details)"


class Tool:
    """Base class for tools."""

    name: str = "base_tool"
    description: str = "A tool"

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool."""
        return ToolResult(success=False, error="Not implemented")


# Global tools registry
TOOLS: Dict[str, Tool] = {}


# Git/GitHub tools removed - available via bash exec (gh CLI, git command)
from .jira import get_tools_schemas as get_jira_tools
from .confluence import get_tools_schemas as get_confluence_tools
from .bash_tools import get_tools_schemas as get_bash_tools

# Also export raw functions for backward compatibility
from . import jira
from . import confluence
from . import bash_tools


def get_all_tools() -> list:
    """Get all tool schemas."""
    tools = []
    tools.extend(get_bash_tools())   # Bash/Shell tools: exec only (simplified)
    # Git/GitHub tools removed - available via bash exec (gh CLI, git command)
    tools.extend(get_jira_tools())
    tools.extend(get_confluence_tools())
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
    from . import bash_tools as bash_tools_module
    from . import jira as jira_module
    from . import confluence as confluence_module
    # Git/GitHub tools removed - available via bash exec (gh CLI, git command)
    
    # Bash/Shell tools
    if name == "read":
        file_path = kwargs.get("file_path", "")
        limit = kwargs.get("limit")
        offset = kwargs.get("offset")
        result = bash_tools_module.read(file_path, limit, offset)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "write":
        file_path = kwargs.get("file_path", "")
        content = kwargs.get("content", "")
        result = bash_tools_module.write(file_path, content)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "edit":
        file_path = kwargs.get("file_path", "")
        oldText = kwargs.get("oldText", "")
        newText = kwargs.get("newText", "")
        result = bash_tools_module.edit(file_path, oldText, newText)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "list_dir":
        path = kwargs.get("path", ".")
        result = bash_tools_module.list_dir(path)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "exec":
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout", 60)
        result = await bash_tools_module.exec(command, timeout)
        # Check for success (not blocked, no error)
        is_success = "Error" not in result and "blocked" not in result.lower() and "requires approval" not in result.lower()
        return ToolResult(success=is_success, content=result)
    
    # Git tools removed - available via bash exec (git command)
    
    # Jira tools
    elif name == "jira_get_issue":
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
    
    elif name == "jira_get_issue_by_url":
        url = kwargs.get("url", "")
        result = await jira_module.jira_get_issue_by_url(url)
        return ToolResult(success="Error" not in result, content=result)
    
    # GitHub tools removed - available via bash exec (gh CLI)
    
    # Confluence tools
    elif name == "confluence_get_page":
        page_id = kwargs.get("page_id", "")
        result = await confluence_module.confluence_get_page(page_id)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_search":
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 10)
        result = await confluence_module.confluence_search(query, max_results)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_get_page_by_url":
        url = kwargs.get("url", "")
        result = await confluence_module.confluence_get_page_by_url(url)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_create_page":
        space_key = kwargs.get("space_key", "")
        title = kwargs.get("title", "")
        body = kwargs.get("body", "")
        parent_id = kwargs.get("parent_id")
        result = await confluence_module.confluence_create_page(space_key, title, body, parent_id)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_update_page":
        page_id = kwargs.get("page_id", "")
        title = kwargs.get("title")
        body = kwargs.get("body")
        result = await confluence_module.confluence_update_page(page_id, title, body)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_delete_page":
        page_id = kwargs.get("page_id", "")
        result = await confluence_module.confluence_delete_page(page_id)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_get_comments":
        page_id = kwargs.get("page_id", "")
        result = await confluence_module.confluence_get_comments(page_id)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_add_comment":
        page_id = kwargs.get("page_id", "")
        comment = kwargs.get("comment", "")
        result = await confluence_module.confluence_add_comment(page_id, comment)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_list_spaces":
        limit = kwargs.get("limit", 20)
        result = await confluence_module.confluence_list_spaces(limit)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_get_space":
        space_key = kwargs.get("space_key", "")
        result = await confluence_module.confluence_get_space(space_key)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_list_pages":
        space_key = kwargs.get("space_key", "")
        limit = kwargs.get("limit", 20)
        result = await confluence_module.confluence_list_pages(space_key, limit)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_get_page_children":
        page_id = kwargs.get("page_id", "")
        limit = kwargs.get("limit", 10)
        result = await confluence_module.confluence_get_page_children(page_id, limit)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_get_page_history":
        page_id = kwargs.get("page_id", "")
        result = await confluence_module.confluence_get_page_history(page_id)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_get_user":
        user_id = kwargs.get("user_id")
        username = kwargs.get("username")
        result = await confluence_module.confluence_get_user(user_id, username)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_watch_page":
        page_id = kwargs.get("page_id", "")
        result = await confluence_module.confluence_watch_page(page_id)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_unwatch_page":
        page_id = kwargs.get("page_id", "")
        result = await confluence_module.confluence_unwatch_page(page_id)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_search_by_title":
        title = kwargs.get("title", "")
        space_key = kwargs.get("space_key")
        result = await confluence_module.confluence_search_by_title(title, space_key)
        return ToolResult(success="Error" not in result, content=result)
    
    return ToolResult(success=False, error=f"Tool {name} not implemented")


__all__ = [
    "get_all_tools",
    "get_tool_names",
    "get_tool",
    "get_tools_schema",
    "execute_tool",
    # Git/GitHub tools removed - available via bash exec
    "get_jira_tools",
    "get_confluence_tools",
    "get_bash_tools",
    "jira",
    "confluence",
    "bash_tools",
    "ToolResult",
    "Tool",
    "TOOLS",
]
