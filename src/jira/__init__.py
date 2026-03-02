"""Jira Integration - Single source of truth for Jira operations."""

import logging
from typing import List, Optional

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
) -> str:
    """Get a Jira issue by key.
    
    Args:
        issue_key: Jira issue key (e.g., 'PROJ-123')
        format: Output format - "markdown" (default), "wiki", or "raw"
        max_chars: Maximum characters to return
        max_comments: Maximum number of comments to include
        include_fields: Fields to include (default: summary, status, description, comments)
        include_comments: Whether to include comments
        
    Returns:
        Issue details in requested format
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
    
    # Extract issue key from URL
    match = re.search(r'/browse/([A-Z]+-\d+)', url)
    if not match:
        return f"Could not extract issue key from URL: {url}"
    
    issue_key = match.group(1)
    
    # Get the correct instance client based on URL
    instance_channel = jira_channel.get_instance_client(url=url)
    adapter = JiraFormatAdapter(instance_channel)
    
    return await adapter.get_issue(
        issue_key=issue_key,
        format=format,
        max_chars=max_chars,
        max_comments=max_comments,
        include_fields=include_fields,
        include_comments=include_comments
    )


async def jira_add_comment(
    issue_key: str,
    body: str,
    body_format: str = "markdown"
) -> str:
    """Add a comment to a Jira issue.
    
    Args:
        issue_key: Jira issue key
        body: Comment body (Markdown by default)
        body_format: Input format - "markdown" (default), "wiki", or "raw"
        
    Returns:
        Success message
    """
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


# ========== Helper function for legacy compatibility ==========
async def _jira_get_issue_with_channel(issue_key: str, channel) -> str:
    """Get Jira issue using a specific channel instance.
    
    Args:
        issue_key: Jira issue key (e.g., PROJ-123)
        channel: JiraChannel instance to use
    
    Returns:
        Issue details
    """
    import httpx
    import logging
    
    logger = logging.getLogger(__name__)
    
    if not channel.is_configured():
        logger.warning("_jira_get_issue_with_channel: Channel not configured")
        return "Error: Jira not configured"
    
    try:
        logger.info(f"Fetching issue: {issue_key}")
        issue = await channel.get_issue(issue_key)
        fields = issue.get("fields", {})
        
        status = fields.get("status", {}).get("name", "Unknown")
        assignee = fields.get("assignee", {})
        assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        summary = fields.get("summary", "")
        description = channel._parse_body(fields.get("description", ""))
        
        logger.debug(f"jira_get_issue: {issue_key} found, status={status}")
        
        return f"""**{issue_key}: {summary}**

**Status:** {status}
**Assignee:** {assignee_name}
**Priority:** {fields.get("priority", {}).get("name", "None") if channel.api_version == "3" else "N/A"}
**Type:** {fields.get("issuetype", {}).get("name", "Task")}
**Created:** {fields.get("created", "")[:10]}
**Updated:** {fields.get("updated", "")[:10]}

**Description:**
{description}"""
    except httpx.HTTPStatusError as e:
        logger.error(f"jira_get_issue: HTTP error {e.response.status_code} for {issue_key}")
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.exception(f"jira_get_issue: Failed to fetch {issue_key}")
        return f"Error getting issue {issue_key}: {str(e)}"


# Add URL-based lookup tool
async def jira_get_issue_by_url(url: str) -> str:
    """Get a Jira issue directly by its URL.
    
    Uses the URL to find the correct Jira instance automatically.
    
    Args:
        url: Full Jira issue URL (e.g., https://company.atlassian.net/browse/PROJ-123)
    
    Returns:
        Issue details including summary, status, assignee, description
    """
    import re
    
    # Extract issue key from URL
    # Format: https://domain/browse/PROJ-123
    match = re.search(r'/browse/([A-Z]+-\d+)', url)
    if not match:
        return f"Could not extract issue key from URL: {url}"
    
    issue_key = match.group(1)
    
    # Get the correct instance client based on URL
    instance_channel = jira_channel.get_instance_client(url=url)
    
    # Use the instance channel to fetch the issue
    return await _jira_get_issue_with_channel(issue_key, instance_channel)


# Re-export get_tools_schemas with additional tool
from .api import get_tools_schemas as _get_tools_schemas

def get_tools_schemas() -> list:
    """Get all Jira tool schemas including URL-based lookup."""
    tools = _get_tools_schemas()
    
    # Add URL-based lookup tool
    tools.append({
        "type": "function",
        "function": {
            "name": "jira_get_issue_by_url",
            "description": "Get a Jira issue directly by its full URL. Use this when user provides a Jira issue URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full Jira issue URL (e.g., https://company.atlassian.net/browse/PROJ-123)"}
                },
                "required": ["url"]
            }
        }
    })
    
    return tools
