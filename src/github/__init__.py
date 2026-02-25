"""GitHub Integration - Single source of truth for GitHub operations."""

from .api import GitHubChannel as GitHubClient, github_channel, github_get_issue, github_search_issues, github_add_comment

__all__ = ["GitHubClient", "github_channel", "github_get_issue", "github_search_issues", "github_add_comment"]


# ========== Tool Functions ==========

async def github_get_issue(owner: str, repo: str, issue_number: int) -> str:
    """Get GitHub issue or PR details."""
    try:
        issue = await github_channel.get_issue(owner, repo, issue_number)
        state = issue.get("state", "unknown")
        title = issue.get("title", "Untitled")
        body = issue.get("body", "")
        return f"**{owner}/{repo}#{issue_number}: {title}**\n\n**State:** {state}\n\n{body}"
    except Exception as e:
        return f"Error getting issue: {e}"


async def github_search_issues(query: str, max_results: int = 10) -> str:
    """Search GitHub issues and PRs."""
    try:
        result = await github_channel.search_issues(query, max_results)
        items = result.get("items", [])
        if not items:
            return "No issues found."
        lines = [f"**Search Results** ({len(items)}):\n"]
        for item in items:
            num = item.get("number")
            title = item.get("title", "")[:40]
            state = item.get("state")
            repo_url = item.get("repository_url") or ""
            repo = repo_url.split("/")[-1] if repo_url else "unknown"
            lines.append(f"- **{repo}#{num}** [{state}] {title}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching: {e}"


async def github_add_comment(owner: str, repo: str, issue_number: int, comment: str) -> str:
    """Add a comment to a GitHub issue or PR."""
    try:
        result = await github_channel.add_comment(owner, repo, issue_number, comment)
        comment_id = result.get("id", "unknown")
        return f"Comment added: {owner}/{repo}#{issue_number} (ID: {comment_id})"
    except Exception as e:
        return f"Error adding comment: {e}"


def get_tools_schemas() -> list:
    """Return GitHub tool schemas for OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": "github_get_issue",
                "description": "Get GitHub issue or PR details",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "issue_number": {"type": "integer", "description": "Issue or PR number"}
                    },
                    "required": ["owner", "repo", "issue_number"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_search_issues",
                "description": "Search GitHub issues and PRs",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer", "description": "Maximum results", "default": 10}
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_add_comment",
                "description": "Add a comment to a GitHub issue or PR",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "issue_number": {"type": "integer", "description": "Issue or PR number"},
                        "comment": {"type": "string", "description": "Comment text"}
                    },
                    "required": ["owner", "repo", "issue_number", "comment"]
                }
            }
        },
    ]
