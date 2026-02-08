"""Jira Integration - Single source of truth for Jira operations."""

from .api import JiraChannel

__all__ = ["JiraChannel"]


# ========== Tool Functions ==========

async def jira_get_issue(issue_key: str) -> str:
    """Get a Jira issue by key."""
    try:
        issue = await jira_client.get_issue(issue_key)
        return f"**{issue_key}: {issue.get('summary', 'No summary')}**\n\n**Status:** {issue.get('status', 'Unknown')}\n\n{issue.get('description', 'No description')}..."
    except Exception as e:
        return f"Error getting issue: {e}"


async def jira_search(jql: str, max_results: int = 10) -> str:
    """Search Jira issues using JQL."""
    try:
        result = await jira_client.search_issues(jql, max_results)
        issues = result.get("issues", [])
        if not issues:
            return "No issues found."
        lines = [f"**Search Results** ({len(issues)}):\n"]
        for issue in issues:
            key = issue.get("key", "Unknown")
            summary = issue.get("fields", {}).get("summary", "")[:40]
            status = issue.get("fields", {}).get("status", {}).get("name", "Unknown")
            lines.append(f"- **{key}** [{status}] {summary}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching: {e}"


async def jira_add_comment(issue_key: str, comment: str) -> str:
    """Add a comment to a Jira issue."""
    try:
        result = await jira_client.add_comment(issue_key, comment)
        return f"Comment added to {issue_key}"
    except Exception as e:
        return f"Error adding comment: {e}"


def get_tools_schemas() -> list:
    """Return Jira tool schemas for OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": "jira_get_issue",
                "description": "Get a Jira issue by key",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_search",
                "description": "Search Jira issues using JQL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "jql": {"type": "string", "description": "JQL query"},
                        "max_results": {"type": "integer", "description": "Maximum results", "default": 10}
                    },
                    "required": ["jql"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_add_comment",
                "description": "Add a comment to a Jira issue",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key"},
                        "comment": {"type": "string", "description": "Comment text"}
                    },
                    "required": ["issue_key", "comment"]
                }
            }
        },
    ]
