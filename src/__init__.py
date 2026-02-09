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
from .bash_tools import get_tools_schemas as get_bash_tools

# Also export raw functions for backward compatibility
from . import github
from . import jira
from . import confluence
from . import git
from . import bash_tools


def get_all_tools() -> list:
    """Get all tool schemas."""
    tools = []
    tools.extend(get_bash_tools())   # Bash/Shell tools: read, write, edit, list_dir, exec
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
    from . import bash_tools as bash_tools_module
    from . import git as git_module
    from . import jira as jira_module
    from . import github as github_module
    from . import confluence as confluence_module
    
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
        workdir = kwargs.get("workdir")
        env = kwargs.get("env")
        security = kwargs.get("security")
        ask = kwargs.get("ask")
        result = await bash_tools_module.exec(command, timeout, workdir, env, security, ask)
        # Check for success (not blocked, no error)
        is_success = "Error" not in result and "blocked" not in result.lower() and "requires approval" not in result.lower()
        return ToolResult(success=is_success, content=result)
    
    # Git tools
    elif name == "git_status":
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
    
    # GitHub tools
    elif name == "github_get_issue":
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
    "get_bash_tools",
    "github_api",
    "jira",
    "confluence",
    "git_api",
    "bash_tools",
    "ToolResult",
    "Tool",
    "TOOLS",
]
