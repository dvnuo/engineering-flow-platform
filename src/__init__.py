"""
Bash Tools - Shell execution and file operations.

Bash tools with security controls.
"""

from typing import Any, Dict, List, Optional
import json


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
from .context_tools import get_tools_schemas as get_context_tools

# Also export raw functions for backward compatibility
from . import github
from . import jira
from . import confluence
from . import git
from . import bash_tools
from . import context_tools
from .tools_external import get_external_tool_registry


def _extract_tool_schema_name(tool_schema: Dict[str, Any]) -> str:
    if not isinstance(tool_schema, dict):
        return ""
    if isinstance(tool_schema.get("name"), str) and tool_schema.get("name").strip():
        return tool_schema["name"].strip()
    function_obj = tool_schema.get("function")
    if isinstance(function_obj, dict):
        name = function_obj.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return ""


def _get_legacy_tools_schema() -> list:
    tools = []
    tools.extend(get_bash_tools())
    tools.extend(get_github_tools())
    tools.extend(get_jira_tools())
    tools.extend(get_confluence_tools())
    tools.extend(get_git_tools())
    tools.extend(get_context_tools())
    return tools


def _get_legacy_tool_names_set() -> set[str]:
    return {
        name
        for name in (_extract_tool_schema_name(item) for item in _get_legacy_tools_schema())
        if name
    }


def is_external_tool_exposed(name: str) -> bool:
    """Return True when an external tool is visible in the merged tool surface."""
    registry = get_external_tool_registry()
    legacy_names = _get_legacy_tool_names_set()
    return registry.has_tool(name) and (name not in legacy_names or registry.is_override_enabled(name))


def get_all_tools() -> list:
    """Get all tool schemas, merging legacy built-ins with external tools."""
    legacy_tools = _get_legacy_tools_schema()
    legacy_names = {name for name in (_extract_tool_schema_name(item) for item in legacy_tools) if name}
    result = list(legacy_tools)

    registry = get_external_tool_registry()
    for schema in registry.get_tools_schema():
        name = _extract_tool_schema_name(schema)
        if not name:
            continue
        if name in legacy_names:
            if registry.is_override_enabled(name):
                result = [item for item in result if _extract_tool_schema_name(item) != name]
                result.append(schema)
            else:
                continue
        else:
            result.append(schema)
    return result


def get_tool_names() -> List[str]:
    """Get all tool names."""
    return [name for name in (_extract_tool_schema_name(t) for t in get_all_tools()) if name]


def get_tool(name: str) -> Optional[Dict]:
    """Get tool schema by name."""
    for tool in get_all_tools():
        if _extract_tool_schema_name(tool) == name:
            return tool
    return None


def get_tool_map() -> Dict[str, Dict[str, Any]]:
    """Get tool schema map by tool name."""
    return {name: tool for tool in get_all_tools() if (name := _extract_tool_schema_name(tool))}


def get_tools_schema() -> List[Dict]:
    """Get all tool schemas for LLM context."""
    return get_all_tools()


def _strip_none_values(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Remove keys explicitly set to None so Python defaults can apply."""
    return {k: v for k, v in kwargs.items() if v is not None}


async def execute_tool(name: str, **kwargs) -> ToolResult:
    """Execute a tool by name."""
    from . import bash_tools as bash_tools_module
    from . import git as git_module
    from . import jira as jira_module
    from . import github as github_module
    from . import confluence as confluence_module
    from . import context_tools as context_tools_module
    kwargs = _strip_none_values(kwargs)

    # Backward compatibility: map exec to run_command before external/legacy dispatch.
    if name == "exec":
        name = "run_command"
        if "command" in kwargs:
            kwargs["cmd"] = kwargs.pop("command")
        if "timeout" in kwargs:
            kwargs["timeout_ms"] = kwargs.pop("timeout") * 1000

    registry = get_external_tool_registry()
    legacy_names = _get_legacy_tool_names_set()

    if registry.has_tool(name) and registry.is_override_enabled(name):
        external_result = await registry.execute_tool(name, **kwargs)
        return ToolResult(
            success=external_result.success,
            content=external_result.content,
            error=external_result.error,
        )

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
        format = kwargs.get("format")
        format = "markdown" if format is None else format
        max_chars = kwargs.get("max_chars")
        max_comments = kwargs.get("max_comments")
        max_comments = 5 if max_comments is None else max_comments
        include_comments = kwargs.get("include_comments")
        include_comments = True if include_comments is None else include_comments
        include_fields = kwargs.get("include_fields")
        include_attachment_urls = kwargs.get("include_attachment_urls")
        include_attachment_urls = False if include_attachment_urls is None else include_attachment_urls
        result = await jira_module.jira_get_issue(
            issue_key, format=format, max_chars=max_chars, max_comments=max_comments,
            include_comments=include_comments, include_fields=include_fields,
            include_attachment_urls=include_attachment_urls,
            preview=kwargs.get("preview", False),
            _session_id=kwargs.get("_session_id"),
        )
        # Ensure content is always a string (format="raw" returns dict)
        if isinstance(result, dict):
            result = str(result)
        return ToolResult(success="Error" not in result, content=result)

    elif name == "jira_get_issue_preview":
        result = await jira_module.jira_get_issue_preview(
            kwargs.get("issue_key", ""),
            format=kwargs.get("format", "markdown"),
        )
        return ToolResult(success="Error" not in str(result), content=str(result))
    
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

    elif name == "jira_create_issue":
        project_key = kwargs.get("project_key", "")
        summary = kwargs.get("summary", "")
        description = kwargs.get("description", "")
        description_format = kwargs.get("description_format", "markdown")
        issue_type = kwargs.get("issue_type", "Task")
        priority = kwargs.get("priority")
        assignee = kwargs.get("assignee")
        labels = kwargs.get("labels")
        result = await jira_module.jira_create_issue(
            project_key, summary, description,
            description_format=description_format,
            issue_type=issue_type,
            priority=priority,
            assignee=assignee,
            labels=labels
        )
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_get_issue_by_url":
        url = kwargs.get("url", "")
        format = kwargs.get("format")
        format = "markdown" if format is None else format
        max_chars = kwargs.get("max_chars")
        max_comments = kwargs.get("max_comments")
        max_comments = 5 if max_comments is None else max_comments
        include_comments = kwargs.get("include_comments")
        include_comments = True if include_comments is None else include_comments
        include_fields = kwargs.get("include_fields")
        include_attachment_urls = kwargs.get("include_attachment_urls")
        include_attachment_urls = False if include_attachment_urls is None else include_attachment_urls
        result = await jira_module.jira_get_issue_by_url(
            url, format=format, max_chars=max_chars, max_comments=max_comments,
            include_comments=include_comments, include_fields=include_fields,
            include_attachment_urls=include_attachment_urls,
            preview=kwargs.get("preview", False),
            _session_id=kwargs.get("_session_id"),
        )
        # Ensure content is always a string (format="raw" returns dict)
        if isinstance(result, dict):
            result = str(result)
        return ToolResult(success="Error" not in result, content=result)

    elif name == "jira_get_issue_by_url_preview":
        result = await jira_module.jira_get_issue_by_url_preview(
            kwargs.get("url", ""),
            format=kwargs.get("format", "markdown"),
        )
        return ToolResult(success="Error" not in str(result), content=str(result))

    elif name == "jira_prepare_issue_context":
        result = await jira_module.jira_prepare_issue_context(
            issue_key_or_url=kwargs.get("issue_key_or_url", ""),
            include_all_comments=True if kwargs.get("include_all_comments") is None else kwargs.get("include_all_comments"),
            include_attachments=True if kwargs.get("include_attachments") is None else kwargs.get("include_attachments"),
            include_raw_snapshot=True if kwargs.get("include_raw_snapshot") is None else kwargs.get("include_raw_snapshot"),
            _session_id=kwargs.get("_session_id"),
        )
        if isinstance(result, dict):
            result = str(result)
        return ToolResult(success="Error" not in str(result), content=result)
    
    elif name == "jira_add_attachment":
        issue_key = kwargs.get("issue_key", "")
        file_path = kwargs.get("file_path", "")
        result = await jira_module.jira_add_attachment(issue_key, file_path)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_get_projects":
        result = await jira_module.jira_get_projects()
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_get_transitions":
        issue_key = kwargs.get("issue_key", "")
        result = await jira_module.jira_get_transitions(issue_key)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_transition":
        issue_key = kwargs.get("issue_key", "")
        transition_id = kwargs.get("transition_id", "")
        result = await jira_module.jira_transition(issue_key, transition_id)
        return ToolResult(success="Error" not in result, content=result)
    
    elif name == "jira_assign_issue":
        issue_key = kwargs.get("issue_key", "")
        assignee = kwargs.get("assignee", "")
        result = await jira_module.jira_assign_issue(issue_key, assignee)
        return ToolResult(success="Error" not in result, content=result)

    elif name == "jira_export_issues_to_markdown":
        result = await jira_module.jira_export_issues_to_markdown(
            input=kwargs.get("input"),
            issue_keys=kwargs.get("issue_keys"),
            jql=kwargs.get("jql"),
            page_size=kwargs.get("page_size", 50),
            max_issues=kwargs.get("max_issues", 100),
            output_mode=kwargs.get("output_mode", "auto"),
            output_directory=kwargs.get("output_directory"),
            download_attachments=kwargs.get("download_attachments"),
            attachments_dir=kwargs.get("attachments_dir", "attachments"),
            include_raw_snapshot=kwargs.get("include_raw_snapshot", False),
            include_coverage_ledger=kwargs.get("include_coverage_ledger", True),
            max_comments=kwargs.get("max_comments", 10),
            comments_order=kwargs.get("comments_order", "latest_first"),
            attachments_concurrency=kwargs.get("attachments_concurrency", 4),
            attachments_max_size=kwargs.get("attachments_max_size", 52428800),
            attachments_inline_text_threshold=kwargs.get("attachments_inline_text_threshold", 2000),
            attachments_retries=kwargs.get("attachments_retries", 3),
            attachments_backoff=kwargs.get("attachments_backoff", [1, 2, 4]),
            attachments_preserve_binary=kwargs.get("attachments_preserve_binary", True),
            _session_id=kwargs.get("_session_id"),
        )
        ok = isinstance(result, dict) and result.get("status") in {"success", "partial"}
        first_error = None
        if isinstance(result, dict):
            errs = result.get("errors") or []
            if errs:
                first_error = errs[0] if isinstance(errs[0], str) else str(errs[0])
        return ToolResult(
            success=bool(ok),
            content=json.dumps(result, ensure_ascii=False, indent=2),
            error=None if ok else first_error,
        )

    # GitHub tools
    elif name == "github_get_issue":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        issue_number = kwargs.get("issue_number", 0)
        result = await github_module.github_get_issue(owner, repo, issue_number)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)

    elif name == "github_get_pr":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        pull_number = kwargs.get("pull_number", 0)
        result = await github_module.github_get_pr(owner, repo, pull_number)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_search_issues":
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 10)
        result = await github_module.github_search_issues(query, max_results)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_add_comment":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        issue_number = kwargs.get("issue_number", 0)
        comment = kwargs.get("comment", "")
        result = await github_module.github_add_comment(owner, repo, issue_number, comment)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_get_pr_files":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        pull_number = kwargs.get("pull_number", 0)
        result = await github_module.github_get_pr_files(owner, repo, pull_number)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)

    elif name == "github_get_pr_file_patch":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        pull_number = kwargs.get("pull_number", 0)
        path = kwargs.get("path", "")
        result = await github_module.github_get_pr_file_patch(owner, repo, pull_number, path)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_get_pr_diff":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        pull_number = kwargs.get("pull_number", 0)
        result = await github_module.github_get_pr_diff(owner, repo, pull_number)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_get_pr_comments":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        pull_number = kwargs.get("pull_number", 0)
        result = await github_module.github_get_pr_comments(owner, repo, pull_number)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_add_pr_review_comment":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        pull_number = kwargs.get("pull_number", 0)
        body = kwargs.get("body", "")
        commit_id = kwargs.get("commit_id")
        path = kwargs.get("path")
        line = kwargs.get("line")
        event = kwargs.get("event", "COMMENT")
        result = await github_module.github_add_pr_review_comment(
            owner=owner,
            repo=repo,
            pull_number=pull_number,
            body=body,
            commit_id=commit_id,
            path=path,
            line=line,
            event=event,
        )
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_list_pr_reviews":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        pull_number = kwargs.get("pull_number", 0)
        result = await github_module.github_list_pr_reviews(owner, repo, pull_number)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_list_branches":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        result = await github_module.github_list_branches(owner, repo)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_get_default_branch":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        result = await github_module.github_get_default_branch(owner, repo)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_create_branch":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        branch_name = kwargs.get("branch_name", "")
        from_branch = kwargs.get("from_branch")
        result = await github_module.github_create_branch(owner, repo, branch_name, from_branch)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_get_file_content":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        path = kwargs.get("path", "")
        branch = kwargs.get("branch")
        session_id = kwargs.get("_session_id")
        preview = kwargs.get("preview", True)
        result = await github_module.github_get_file_content(
            owner,
            repo,
            path,
            branch,
            _session_id=session_id,
            preview=preview,
        )
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_prepare_issue_context":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        issue_number = kwargs.get("issue_number", 0)
        session_id = kwargs.get("_session_id")
        result = await github_module.github_prepare_issue_context(
            owner,
            repo,
            issue_number,
            _session_id=session_id,
        )
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)

    elif name == "github_prepare_pr_context":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        pull_number = kwargs.get("pull_number", 0)
        session_id = kwargs.get("_session_id")
        result = await github_module.github_prepare_pr_context(
            owner,
            repo,
            pull_number,
            _session_id=session_id,
        )
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_create_pull_request":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        title = kwargs.get("title", "")
        body = kwargs.get("body", "")
        head = kwargs.get("head", "")
        base = kwargs.get("base", "main")
        result = await github_module.github_create_pull_request(owner, repo, title, body, head, base)
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    elif name == "github_create_or_update_file":
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        message = kwargs.get("message", "")
        sha = kwargs.get("sha")
        branch = kwargs.get("branch", "")
        result = await github_module.github_create_or_update_file(
            owner, repo, path, content, message, sha, branch
        )
        return ToolResult(success=not result.lstrip().startswith("Error"), content=result)
    
    # Confluence tools
    elif name == "confluence_get_page":
        page_id = kwargs.get("page_id", "")
        format = kwargs.get("format", "markdown")
        max_chars = kwargs.get("max_chars")
        result = await confluence_module.confluence_get_page(
            page_id,
            format=format,
            max_chars=max_chars,
            preview=kwargs.get("preview", False),
            _session_id=kwargs.get("_session_id"),
        )
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
        result = await confluence_module.confluence_get_page_by_url(
            url,
            format=format,
            max_chars=max_chars,
            preview=kwargs.get("preview", False),
            _session_id=kwargs.get("_session_id"),
        )
        return ToolResult(success="Error" not in result, content=result)

    elif name == "confluence_get_page_preview":
        result = await confluence_module.confluence_get_page_preview(kwargs.get("page_id", ""), format=kwargs.get("format", "markdown"))
        return ToolResult(success="Error" not in str(result), content=str(result))

    elif name == "confluence_get_page_by_url_preview":
        result = await confluence_module.confluence_get_page_by_url_preview(kwargs.get("url", ""), format=kwargs.get("format", "markdown"))
        return ToolResult(success="Error" not in str(result), content=str(result))

    elif name == "confluence_prepare_page_context":
        result = await confluence_module.confluence_prepare_page_context(
            page_id_or_url=kwargs.get("page_id_or_url", ""),
            include_comments=True if kwargs.get("include_comments") is None else kwargs.get("include_comments"),
            include_attachments=True if kwargs.get("include_attachments") is None else kwargs.get("include_attachments"),
            include_children=True if kwargs.get("include_children") is None else kwargs.get("include_children"),
            include_raw_snapshot=True if kwargs.get("include_raw_snapshot") is None else kwargs.get("include_raw_snapshot"),
            _session_id=kwargs.get("_session_id"),
        )
        return ToolResult(success="Error" not in str(result), content=str(result))

    elif name == "context_read_ref":
        ref = kwargs.get("ref", "")
        section = kwargs.get("section")
        start = kwargs.get("start")
        max_chars = kwargs.get("max_chars", 6000)
        _session_id = kwargs.get("_session_id")
        result = await context_tools_module.context_read_ref(
            ref=ref,
            section=section,
            start=start,
            max_chars=max_chars,
            _session_id=_session_id,
        )
        return ToolResult(success=True, content=result)
    
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
        result = await confluence_module.confluence_get_comments(page_id, _session_id=kwargs.get("_session_id"))
        return ToolResult(success="Error" not in result, content=result)

    elif name == "jira_get_comments":
        issue_key = kwargs.get("issue_key", "")
        result = await jira_module.jira_get_comments(issue_key, _session_id=kwargs.get("_session_id"))
        return ToolResult(success="Error" not in str(result), content=str(result))
    
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
        result = await confluence_module.confluence_get_page_children(
            page_id,
            limit,
            _session_id=kwargs.get("_session_id"),
        )
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
    
    if registry.has_tool(name) and name not in legacy_names:
        external_result = await registry.execute_tool(name, **kwargs)
        return ToolResult(
            success=external_result.success,
            content=external_result.content,
            error=external_result.error,
        )

    return ToolResult(success=False, error=f"Tool {name} not implemented")


__all__ = [
    "get_all_tools",
    "get_tool_names",
    "get_tool",
    "get_tool_map",
    "get_tools_schema",
    "is_external_tool_exposed",
    "execute_tool",
    "get_github_tools",
    "get_jira_tools",
    "get_confluence_tools",
    "get_git_tools",
    "get_bash_tools",
    "jira",
    "confluence",
    "bash_tools",
    "ToolResult",
    "Tool",
    "TOOLS",
]
