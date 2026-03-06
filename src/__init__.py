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
    tools.extend(get_bash_tools())   # Bash/Shell tools: exec only (simplified)
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
    
    # Backward compatibility: map exec to run_command
    if name == "exec":
        name = "run_command"
        # Map old exec args to new format
        if "command" in kwargs:
            kwargs["cmd"] = kwargs.pop("command")
        if "timeout" in kwargs:
            kwargs["timeout_ms"] = kwargs.pop("timeout") * 1000
    
    elif name == "run_command":
        cmd = kwargs.get("cmd", "")
        args = kwargs.get("args") or []
        cwd = kwargs.get("cwd")
        timeout_ms = kwargs.get("timeout_ms", 15000)
        result = await bash_tools_module.run_command(cmd, args, cwd, timeout_ms)
        
        # Include more info: exit_code, stderr, truncated
        exit_code = result.get("exit_code", 0)
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        truncated = result.get("truncated", {})
        
        output = stdout
        if stderr:
            output += "\n[stderr: " + stderr + "]"
        if truncated.get("stdout"):
            output += "\n[stdout truncated]"
        if truncated.get("stderr"):
            output += "\n[stderr truncated]"
        if exit_code != 0:
            output += "\n[exit code: " + str(exit_code) + "]"
        
        is_success = result.get("ok", False)
        return ToolResult(success=is_success, content=output)
    
    elif name == "discover_commands":
        prefix = kwargs.get("prefix")
        contains = kwargs.get("contains")
        limit = kwargs.get("limit", 200)
        result = await bash_tools_module.discover_commands(prefix, contains, limit=limit)
        return ToolResult(success=True, content=str(result))
    
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
        format = kwargs.get("format", "markdown")
        max_chars = kwargs.get("max_chars")
        max_comments = kwargs.get("max_comments", 5)
        include_comments = kwargs.get("include_comments", True)
        include_fields = kwargs.get("include_fields")
        result = await jira_module.jira_get_issue(
            issue_key, format=format, max_chars=max_chars, max_comments=max_comments,
            include_comments=include_comments, include_fields=include_fields
        )
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_search":
        jql = kwargs.get("jql", "")
        max_results = kwargs.get("max_results", 10)
        result = await jira_module.jira_search(jql, max_results)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_add_comment":
        issue_key = kwargs.get("issue_key", "")
        body = kwargs.get("body") or kwargs.get("comment", "")
        body_format = kwargs.get("body_format", "markdown")
        result = await jira_module.jira_add_comment(issue_key, body, body_format=body_format)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_get_issue_by_url":
        url = kwargs.get("url", "")
        format = kwargs.get("format", "markdown")
        max_chars = kwargs.get("max_chars")
        max_comments = kwargs.get("max_comments", 5)
        include_comments = kwargs.get("include_comments", True)
        include_fields = kwargs.get("include_fields")
        result = await jira_module.jira_get_issue_by_url(
            url, format=format, max_chars=max_chars, max_comments=max_comments,
            include_comments=include_comments, include_fields=include_fields
        )
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
        format = kwargs.get("format", "markdown")
        max_chars = kwargs.get("max_chars")
        result = await confluence_module.confluence_get_page(page_id, format=format, max_chars=max_chars)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_search":
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 10)
        result = await confluence_module.confluence_search(query, max_results)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "confluence_get_page_by_url":
        url = kwargs.get("url", "")
        format = kwargs.get("format", "markdown")
        max_chars = kwargs.get("max_chars")
        result = await confluence_module.confluence_get_page_by_url(url, format=format, max_chars=max_chars)
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
