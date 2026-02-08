"""
GitHub Channel - Backward compatible API.

This module re-exports from src/integrations/github/ for backward compatibility.
"""

from src.github import GitHubClient

# Global instance for backward compatibility
github_channel = GitHubClient()

# Keep old function names for compatibility
async def github_get_issue(owner: str, repo: str, issue_number: int):
    """Get issue or PR details."""
    return await github_channel.get_issue(owner, repo, issue_number)


async def github_search_issues(query: str, max_results: int = 10):
    """Search issues and PRs."""
    return await github_channel.search_issues(query, max_results)


async def github_add_comment(owner: str, repo: str, issue_number: int, body: str):
    """Add a comment to an issue or PR."""
    return await github_channel.add_comment(owner, repo, issue_number, body)


# Export classes for direct import
__all__ = ["GitHubClient", "github_channel"]
