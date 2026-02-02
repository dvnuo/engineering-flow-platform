"""
GitHub Channel - Basic API support for GitHub.

Features:
- Get issue/PR comments
- Add comments to issues/PRs
- Search issues
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)


class GitHubChannel:
    """GitHub channel adapter with basic REST API support."""
    
    def __init__(self):
        self.base_url = "https://api.github.com"
        self.token = config.get("github.api_token", "")
        self.enabled = config.get("github.enabled", False)
        
        self.client = httpx.AsyncClient(timeout=30.0)
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "OpenClaw-Mini",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"
    
    def is_configured(self) -> bool:
        """Check if GitHub is properly configured."""
        return bool(self.token and self.enabled)
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make an API request."""
        url = f"{self.base_url}{endpoint}"
        
        response = await self.client.request(
            method, url, headers=self._headers, **kwargs
        )
        
        if response.status_code >= 400:
            error_msg = f"GitHub API error: {response.status_code}"
            try:
                error_msg += f" - {response.json()}"
            except Exception:
                error_msg += f" - {response.text}"
            raise Exception(error_msg)
        
        if response.status_code == 204:
            return {}
        
        return response.json()
    
    async def get_issue_comments(
        self, 
        owner: str, 
        repo: str, 
        issue_number: int
    ) -> List[Dict[str, Any]]:
        """Get comments on an issue or PR.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue or PR number
            
        Returns:
            List of comments
        """
        logger.info(f"Getting comments for {owner}/{repo}#{issue_number}")
        
        result = await self._request(
            "GET", 
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments"
        )
        
        return [
            {
                "id": str(c.get("id", "")),
                "body": c.get("body", ""),
                "author": c.get("user", {}).get("login", "unknown"),
                "created_at": c.get("created_at", ""),
                "url": c.get("html_url", ""),
            }
            for c in result
        ]
    
    async def get_recent_issue_comments(
        self, 
        repo: str, 
        since: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get recent issue/PR comments from a repository.
        
        Args:
            repo: Repository in "owner/repo" format
            since: Only get comments after this time
            
        Returns:
            List of recent comments
        """
        owner, repo_name = repo.split("/", 1)
        
        # Get recent issues (comments are on issues)
        params = {}
        if since:
            params["since"] = since.isoformat()
        
        issues = await self._request(
            "GET", 
            f"/repos/{owner}/{repo_name}/issues",
            params=params
        )
        
        all_comments = []
        for issue in issues[:10]:  # Limit to recent 10 issues
            issue_number = issue.get("number")
            if issue_number:
                comments = await self.get_issue_comments(
                    owner, repo_name, issue_number
                )
                all_comments.extend(comments)
        
        return all_comments
    
    async def add_comment(
        self, 
        owner: str, 
        repo: str, 
        issue_number: int, 
        body: str
    ) -> Dict[str, Any]:
        """Add a comment to an issue or PR.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue or PR number
            body: Comment body
            
        Returns:
            Created comment data
        """
        logger.info(f"Adding comment to {owner}/{repo}#{issue_number}")
        
        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body}
        )
    
    async def search_issues(
        self, 
        query: str, 
        max_results: int = 10
    ) -> Dict[str, Any]:
        """Search issues and PRs.
        
        Args:
            query: Search query
            max_results: Maximum results to return
            
        Returns:
            Search results
        """
        logger.info(f"Searching issues: {query}")
        
        return await self._request(
            "GET",
            "/search/issues",
            params={"q": query, "per_page": max_results}
        )
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


# Global instance
github_channel = GitHubChannel()


# ========== Tool Functions ==========

async def github_get_issue(owner: str, repo: str, issue_number: int) -> str:
    """Get an issue or PR details."""
    try:
        issue = await github_channel._request(
            "GET", f"/repos/{owner}/{repo}/issues/{issue_number}"
        )
        
        state = issue.get("state", "unknown")
        title = issue.get("title", "Untitled")
        body = issue.get("body", "")[:200]
        
        return f"**{owner}/{repo}#{issue_number}: {title}**\n\n**State:** {state}\n\n{body}..."
    except Exception as e:
        return f"Error getting issue: {e}"


async def github_search_issues(query: str, max_results: int = 10) -> str:
    """Search issues and PRs."""
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
            lines.append(f"- **{item.get('repository_url', '').split('/')[-1]}#{num}** [{state}] {title}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching: {e}"


async def github_add_comment(owner: str, repo: str, issue_number: int, comment: str) -> str:
    """Add a comment to an issue or PR."""
    try:
        result = await github_channel.add_comment(owner, repo, issue_number, comment)
        comment_id = result.get("id", "unknown")
        return f"Comment added: {owner}/{repo}#{issue_number} (ID: {comment_id})"
    except Exception as e:
        return f"Error adding comment: {e}"
