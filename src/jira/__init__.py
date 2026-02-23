"""Jira Integration - Single source of truth for Jira operations."""

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


# Add URL-based lookup tool
async def jira_get_issue_by_url(url: str) -> str:
    """Get a Jira issue directly by its URL.
    
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
    return await jira_get_issue(issue_key)


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
