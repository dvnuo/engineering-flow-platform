"""
GitHub Channel - Basic API support for GitHub.

Features:
- Get issue/PR comments
- Add comments to issues/PRs
- Search issues
- Rate limit handling with exponential backoff

Debug Logging:
- All HTTP requests/responses are logged with full details
- Request: URL, method, headers (sanitized), params, json
- Response: status, headers, body (truncated if too large)
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)

# Debug mode flag
_DEBUG_MODE = os.environ.get("DEBUG_GITHUB", "").lower() in ("1", "true", "yes")

# Rate limit settings
RATE_LIMIT_RETRIES = 5
INITIAL_BACKOFF = 1.0  # seconds
MAX_BACKOFF = 60.0  # seconds


def _sanitize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Remove sensitive headers for logging."""
    sanitized = {}
    for k, v in headers.items():
        if "authorization" in k.lower():
            sanitized[k] = f"[REDACTED:{len(v)} chars]"
        else:
            sanitized[k] = v
    return sanitized


def _truncate_json(data: Any, max_length: int = 500) -> str:
    """Truncate JSON for logging."""
    text = json.dumps(data, indent=2, default=str)
    if len(text) <= max_length:
        return text
    return text[:max_length] + f"... [{len(text) - max_length} chars truncated]"


class GitHubChannel:
    """GitHub channel adapter with basic REST API support.
    
    Supports URL for GitHub configurable base Enterprise instances.
    Configuration:
        github.api_token: API token for authentication
        github.enabled: Whether GitHub integration is enabled
        github.base_url: Base URL for API (default: https://api.github.com)
        github.hostname: Hostname for gh CLI (defaults to base_url hostname)
    """
    
    def __init__(self):
        self.base_url = config.get("github.base_url", "https://api.github.com")
        self.token = config.get("github.api_token", "")
        self.enabled = config.get("github.enabled", False)
        self.hostname = config.get("github.hostname", "")
        
        self.client = httpx.AsyncClient(timeout=30.0)
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "OpsClaw-Mini",
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
        """Make an API request with rate limit handling and exponential backoff."""
        url = f"{self.base_url}{endpoint}"
        
        # Debug: Log request
        if _DEBUG_MODE or logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"=== GITHUB REQUEST ===")
            logger.debug(f"Method: {method}")
            logger.debug(f"URL: {url}")
            logger.debug(f"Headers: {json.dumps(_sanitize_headers(self._headers))}")
            if kwargs.get("params"):
                logger.debug(f"Params: {json.dumps(kwargs['params'])}")
            if kwargs.get("json"):
                logger.debug(f"JSON: {_truncate_json(kwargs['json'])}")
        
        backoff = INITIAL_BACKOFF
        last_error = None
        
        for attempt in range(RATE_LIMIT_RETRIES):
            try:
                response = await self.client.request(
                    method, url, headers=self._headers, **kwargs
                )
                
                # Debug: Log response
                if _DEBUG_MODE or logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"=== GITHUB RESPONSE ===")
                    logger.debug(f"Status: {response.status_code}")
                    logger.debug(f"Headers: {json.dumps(dict(response.headers), default=str)}")
                
                # Handle rate limiting (403)
                if response.status_code == 403:
                    # Check if it's rate limited
                    reset_header = response.headers.get("X-RateLimit-Reset")
                    if reset_header:
                        wait_time = int(reset_header) - int(datetime.utcnow().timestamp())
                        if wait_time > 0:
                            logger.warning(f"GitHub rate limited, waiting {wait_time}s")
                            await asyncio.sleep(min(wait_time + 1, MAX_BACKOFF))
                            continue
                    
                    # Exponential backoff for other 403 errors
                    logger.warning(f"GitHub API 403, attempt {attempt + 1}/{RATE_LIMIT_RETRIES}, backing off {backoff}s")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                    last_error = f"Rate limit: {response.text}"
                    continue
                
                if response.status_code >= 400:
                    error_msg = f"GitHub API error: {response.status_code}"
                    try:
                        error_msg += f" - {response.json()}"
                    except Exception:
                        error_msg += f" - {response.text}"
                    raise Exception(error_msg)
                
                if response.status_code == 204:
                    return {}
                
                result = response.json()
                
                # Debug: Log response body
                if _DEBUG_MODE or logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Body: {_truncate_json(result)}")
                
                return result
                
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = e
                if attempt < RATE_LIMIT_RETRIES - 1:
                    logger.warning(f"GitHub API request failed, attempt {attempt + 1}/{RATE_LIMIT_RETRIES}: {e}")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                else:
                    logger.error(f"GitHub API request failed after {RATE_LIMIT_RETRIES} attempts: {e}")
                    raise
        
        raise Exception(f"GitHub API request failed: {last_error}")
    
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
    
    async def get_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        """Get a single issue or PR.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue or PR number
            
        Returns:
            Issue data
        """
        logger.info(f"Getting issue {owner}/{repo}#{issue_number}")
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/issues/{issue_number}"
        )
    
    async def create_issue(
        self, 
        owner: str, 
        repo: str, 
        title: str, 
        body: str = "",
        labels: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Create a new issue.
        
        Args:
            owner: Repository owner
            repo: Repository name
            title: Issue title
            body: Issue body
            labels: Optional list of labels
            
        Returns:
            Created issue data
        """
        logger.info(f"Creating issue in {owner}/{repo}: {title[:50]}")
        
        data = {"title": title, "body": body}
        if labels:
            data["labels"] = labels
            
        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json=data
        )
    
    async def close_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        """Close an issue or PR.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue or PR number
            
        Returns:
            Updated issue data
        """
        logger.info(f"Closing issue {owner}/{repo}#{issue_number}")
        return await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            json={"state": "closed"}
        )
    
    async def get_pull_request(self, owner: str, repo: str, pull_number: int) -> Dict[str, Any]:
        """Get a pull request.
        
        Args:
            owner: Repository owner
            repo: Repository name
            pull_number: PR number
            
        Returns:
            PR data
        """
        logger.info(f"Getting PR {owner}/{repo}#{pull_number}")
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}"
        )
    
    async def get_file(self, owner: str, repo: str, path: str, ref: str = "") -> Dict[str, Any]:
        """Get file content from a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: File path
            ref: Branch or commit SHA
            
        Returns:
            File data including content (base64 encoded)
        """
        endpoint = f"/repos/{owner}/{repo}/contents/{path}"
        if ref:
            endpoint += f"?ref={ref}"
            
        logger.info(f"Getting file {owner}/{repo}/{path}")
        return await self._request("GET", endpoint)
    
    async def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        sha: Optional[str] = None,
        branch: str = ""
    ) -> Dict[str, Any]:
        """Create or update a file in the repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: File path
            content: File content (will be base64 encoded)
            message: Commit message
            sha: SHA of file being updated (optional, required for updates)
            branch: Branch name
            
        Returns:
            Commit data
        """
        import base64
        
        logger.info(f"{'Updating' if sha else 'Creating'} file {owner}/{repo}/{path}")
        
        data = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
        }
        if sha:
            data["sha"] = sha
        if branch:
            data["branch"] = branch
            
        return await self._request(
            "PUT",
            f"/repos/{owner}/{repo}/contents/{path}",
            json=data
        )
    
    async def list_commits(
        self,
        owner: str,
        repo: str,
        branch: str = "",
        path: str = "",
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """List commits in a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            branch: Branch name (optional)
            path: File path (optional)
            max_results: Maximum results
            
        Returns:
            List of commits
        """
        params = {"per_page": min(max_results, 100)}
        if branch:
            params["sha"] = branch
        if path:
            params["path"] = path
            
        logger.info(f"Listing commits for {owner}/{repo}")
        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/commits",
            params=params
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
