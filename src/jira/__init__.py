"""Jira Integration - Single source of truth for Jira operations."""

import logging

from .api import (
    JiraChannel, 
    jira_channel,
    jira_get_issue,
    jira_search,
    jira_add_comment,
    jira_create_issue,
    jira_transition,
    jira_get_transitions,
    jira_get_comments,
    jira_update_issue,
    jira_assign_issue,
    jira_get_projects,
    jira_get_components,
    jira_get_versions,
    jira_get_worklog,
    jira_add_worklog,
)

__all__ = [
    "JiraChannel", 
    "jira_channel",
    "jira_get_issue",
    "jira_search",
    "jira_add_comment",
    "jira_create_issue",
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


# ========== Helper function for instance-aware operations ==========
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
