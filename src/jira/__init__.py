"""Jira Integration - Single source of truth for Jira operations."""

import logging
from typing import List, Optional, Union

from .api import (
    JiraChannel, 
    jira_channel,
    jira_search,
    jira_transition,
    jira_get_transitions,
    jira_assign_issue,
    jira_get_projects,
    jira_get_components,
    jira_get_versions,
    jira_get_worklog,
    jira_add_worklog,
    jira_get_comments,
)
from .adapter import JiraFormatAdapter

__all__ = [
    "JiraChannel", 
    "jira_channel",
    "jira_get_issue",
    "jira_get_issue_by_url",
    "jira_search",
    "jira_add_comment",
    "jira_create_issue",
    "jira_update_issue",
    "jira_transition",
    "jira_get_transitions",
    "jira_get_comments",
    "jira_update_issue",
    "jira_assign_issue",
    "jira_get_projects",
    "jira_get_components",
    "jira_get_versions",
    "jira_get_worklog",
    "jira_add_worklog",
]


def _get_adapter() -> JiraFormatAdapter:
    """Create a new format adapter bound to the current channel."""
    return JiraFormatAdapter(jira_channel)


# ========== Tool Functions with Markdown Support ==========

async def jira_get_issue(
    issue_key: str,
    format: str = "markdown",
    max_chars: int = None,
    max_comments: int = 5,
    include_fields: List[str] = None,
    include_comments: bool = True
) -> Union[str, dict]:
    """Get a Jira issue by key.
    
    Args:
        issue_key: Jira issue key (e.g., 'PROJ-123')
        format: Output format - "markdown" (default), "wiki", or "raw"
        max_chars: Maximum characters to return
        max_comments: Maximum number of comments to include
        include_fields: Fields to include (default: summary, status, description, comments)
        include_comments: Whether to include comments
        
    Returns:
        Issue details in requested format (markdown/wiki: str, raw: dict)
    """
    try:
        if not jira_channel.is_configured():
            return "Error: Jira is not configured. Please check your settings."
        
        adapter = _get_adapter()
        return await adapter.get_issue(
            issue_key=issue_key,
            format=format,
            max_chars=max_chars,
            max_comments=max_comments,
            include_fields=include_fields,
            include_comments=include_comments
        )
    except Exception as e:
        return f"Error getting issue {issue_key}: {str(e)}"


async def jira_get_issue_by_url(
    url: str,
    format: str = "markdown",
    max_chars: int = None,
    max_comments: int = 5,
    include_fields: List[str] = None,
    include_comments: bool = True
) -> str:
    """Get a Jira issue by its URL.
    
    Args:
        url: Full Jira issue URL (e.g., https://company.atlassian.net/browse/PROJ-123)
        format: Output format - "markdown" (default), "wiki", or "raw"
        max_chars: Maximum characters to return
        max_comments: Maximum number of comments to include
        include_fields: Fields to include
        include_comments: Whether to include comments
        
    Returns:
        Issue details in requested format
    """
    import re
    
    try:
        # Extract issue key from URL
        match = re.search(r'/browse/([A-Z]+-\d+)', url)
        if not match:
            return f"Could not extract issue key from URL: {url}"
        
        issue_key = match.group(1)
        
        # Get the correct instance client based on URL
        if not jira_channel.is_configured():
            return "Error: Jira is not configured."
        
        instance_channel = jira_channel.get_instance_client(url=url)
        
        if not instance_channel.is_configured():
            return f"Error: Jira instance for {url} is not configured."
        
        adapter = JiraFormatAdapter(instance_channel)
        
        return await adapter.get_issue(
            issue_key=issue_key,
            format=format,
            max_chars=max_chars,
            max_comments=max_comments,
            include_fields=include_fields,
            include_comments=include_comments
        )
    except Exception as e:
        return f"Error getting issue from URL: {str(e)}"


async def jira_add_comment(
    issue_key: str,
    body: str = None,
    body_format: str = "markdown",
    comment: str = None
) -> str:
    """Add a comment to a Jira issue.
    
    Args:
        issue_key: Jira issue key
        body: Comment body (Markdown by default) - alias: comment
        body_format: Input format - "markdown" (default), "wiki", or "raw"
        comment: Alias for body
        
    Returns:
        Success message
    """
    # Support both "body" and "comment" parameter names
    body = body or comment or ""
    try:
        if not jira_channel.is_configured():
            return "Error: Jira is not configured."
        
        adapter = _get_adapter()
        return await adapter.add_comment(issue_key, body, body_format=body_format)
    except Exception as e:
        return f"Error adding comment: {str(e)}"


async def jira_create_issue(
    project_key: str,
    summary: str,
    description: str = "",
    description_format: str = "markdown",
    issue_type: str = "Bug",
    priority: str = None,
    assignee: str = None,
    labels: List[str] = None
) -> str:
    """Create a new Jira issue.
    
    Args:
        project_key: Project key (e.g., 'PROJ')
        summary: Issue summary/title
        description: Issue description (Markdown by default)
        description_format: Input format - "markdown" (default), "wiki", or "raw"
        issue_type: Issue type (Task, Bug, Story, etc.)
        priority: Priority name
        assignee: Assignee account ID or email
        labels: List of labels
        
    Returns:
        Success message with issue key and URL
    """
    try:
        if not jira_channel.is_configured():
            return "Error: Jira is not configured."
        
        adapter = _get_adapter()
        return await adapter.create_issue(
            project_key=project_key,
            summary=summary,
            description=description,
            description_format=description_format,
            issue_type=issue_type,
            priority=priority,
            assignee=assignee,
            labels=labels
        )
    except Exception as e:
        return f"Error creating issue: {str(e)}"


async def jira_update_issue(
    issue_key: str,
    summary: str = None,
    description: str = None,
    description_format: str = "markdown",
    priority: str = None,
    labels: List[str] = None
) -> str:
    """Update a Jira issue.
    
    Args:
        issue_key: Jira issue key
        summary: New summary (optional)
        description: New description (optional)
        description_format: Input format - "markdown" (default), "wiki", or "raw"
        priority: New priority (optional)
        labels: New labels (optional)
        
    Returns:
        Success message
    """
    try:
        if not jira_channel.is_configured():
            return "Error: Jira is not configured."
        
        adapter = _get_adapter()
        return await adapter.update_issue(
            issue_key=issue_key,
            summary=summary,
            description=description,
            description_format=description_format,
            priority=priority,
            labels=labels
        )
    except Exception as e:
        return f"Error updating issue: {str(e)}"


# Re-export original functions for compatibility
from .api import jira_get_issue as _original_get_issue
from .api import jira_add_comment as _original_add_comment
from .api import jira_create_issue as _original_create_issue
from .api import jira_update_issue as _original_update_issue
from .api import jira_get_comments


def get_tools_schemas() -> list:
    """Get all Jira tool schemas with Markdown support."""
    # Return updated schemas with new parameters
    return [
        {
            "type": "function",
            "function": {
                "name": "jira_get_issue",
                "description": "Get a Jira issue by key. Returns Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Output format: markdown (LLM-friendly), wiki (renderable), or raw (JSON)"
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum characters to return"
                        },
                        "max_comments": {
                            "type": "integer",
                            "description": "Maximum number of comments to include",
                            "default": 5
                        },
                        "include_fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Fields to include (default: summary, status, description, comments)"
                        },
                        "include_comments": {
                            "type": "boolean",
                            "description": "Whether to include comments",
                            "default": True
                        }
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_issue_by_url",
                "description": "Get a Jira issue by its full URL. Returns Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full Jira issue URL (e.g., https://company.atlassian.net/browse/PROJ-123)"},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Output format: markdown, wiki, or raw"
                        },
                        "max_chars": {"type": "integer", "description": "Maximum characters to return"},
                        "max_comments": {"type": "integer", "description": "Maximum comments to include", "default": 5},
                        "include_comments": {"type": "boolean", "description": "Include comments", "default": True}
                    },
                    "required": ["url"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_create_issue",
                "description": "Create a new Jira issue. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_key": {"type": "string", "description": "Project key (e.g., PROJ)"},
                        "summary": {"type": "string", "description": "Issue summary/title"},
                        "description": {"type": "string", "description": "Issue description (Markdown by default)"},
                        "description_format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Input format: markdown, wiki, or raw"
                        },
                        "issue_type": {"type": "string", "description": "Issue type", "default": "Task"},
                        "priority": {"type": "string", "description": "Priority name"},
                        "labels": {"type": "array", "items": {"type": "string"}, "description": "List of labels"}
                    },
                    "required": ["project_key", "summary"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_update_issue",
                "description": "Update a Jira issue. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "summary": {"type": "string", "description": "New summary (optional)"},
                        "description": {"type": "string", "description": "New description (optional)"},
                        "description_format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Input format: markdown, wiki, or raw"
                        },
                        "priority": {"type": "string", "description": "New priority"},
                        "labels": {"type": "array", "items": {"type": "string"}, "description": "New labels"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_add_comment",
                "description": "Add a comment to a Jira issue. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "comment": {"type": "string", "description": "Comment body (Markdown by default)"},
                        "body_format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Input format: markdown, wiki, or raw"
                        }
                    },
                    "required": ["issue_key", "comment"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_search",
                "description": "Search Jira issues using JQL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "jql": {"type": "string", "description": "JQL query string"},
                        "max_results": {"type": "integer", "description": "Maximum results", "default": 10}
                    },
                    "required": ["jql"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_comments",
                "description": "Get comments on a Jira issue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
    ]
