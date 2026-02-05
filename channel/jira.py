"""
Jira Channel - Backward compatible API.

This module re-exports from src/integrations/jira/ for backward compatibility.
"""

from src.integrations.jira import JiraChannel

# Global instance for backward compatibility
jira_channel = JiraChannel()

# Keep old function names for compatibility
async def jira_get_issue(issue_key: str):
    """Get a Jira issue by key."""
    return await jira_channel.get_issue(issue_key)


async def jira_search(jql: str, max_results: int = 10):
    """Search Jira issues using JQL."""
    return await jira_channel.search_issues(jql, max_results)


async def jira_add_comment(issue_key: str, comment: str):
    """Add a comment to a Jira issue."""
    return await jira_channel.add_comment(issue_key, comment)


async def jira_create_issue(
    project: str,
    summary: str,
    description: str = "",
    issue_type: str = "Task",
    priority: str = "Medium"
):
    """Create a new Jira issue."""
    return await jira_channel.create_issue(
        project=project,
        summary=summary,
        description=description,
        issue_type=issue_type,
        priority=priority
    )


async def jira_transition(issue_key: str, to_status: str, comment: str = ""):
    """Transition a Jira issue to a new status."""
    return await jira_channel.transition_issue(issue_key, to_status, comment)


async def jira_get_transitions(issue_key: str):
    """Get available status transitions for a Jira issue."""
    return await jira_channel.get_transitions(issue_key)


async def jira_get_comments(issue_key: str):
    """Get comments for a Jira issue."""
    return await jira_channel.get_comments(issue_key)


# Export classes for direct import
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
]
