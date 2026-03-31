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
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.utils.truncate import truncate, truncate_json

from src.config import config

logger = logging.getLogger(__name__)

# Debug mode flag
_DEBUG_MODE = os.environ.get("DEBUG_GITHUB", "").lower() in ("1", "true", "yes")

# Rate limit settings
RATE_LIMIT_RETRIES = 5
INITIAL_BACKOFF = 1.0  # seconds
MAX_BACKOFF = 60.0  # seconds


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled."""
    return _DEBUG_MODE or logger.isEnabledFor(logging.DEBUG)


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
    """Truncate JSON for logging (wrapper for truncate_json)."""
    return truncate_json(data, max_length)


class GitHubChannel:
    """GitHub channel adapter with basic REST API support.
    
    Supports URL for GitHub configurable base Enterprise instances.
    Configuration:
        github.api_token: API token for authentication
        github.enabled: Whether GitHub integration is enabled
        github.base_url: Base URL for API (default: (enterprise only - must configure base_url))
        github.hostname: Hostname for gh CLI (defaults to base_url hostname)
    """
    
    def __init__(self):
        self.base_url = config.get("github.base_url", "(enterprise only)")
        self.token = config.get("github.api_token", "")
        self.enabled = config.get("github.enabled", False)
        self.hostname = config.get("github.hostname", "")
        
        self.client = httpx.AsyncClient(timeout=30.0)
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Engineering Flow Platform-Mini",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"
    
    def is_configured(self) -> bool:
        """Check if GitHub is properly configured."""
        return bool(self.token and self.enabled)
    
    def reinit(self):
        """Reinitialize GitHubChannel (called when config changes)."""
        logger.info("Reinitializing GitHubChannel...")
        github_config = config.github or {}
        self.base_url = github_config.get("base_url", "(enterprise only)")
        self.token = github_config.get("api_token", "")
        self.enabled = github_config.get("enabled", False)
        self.hostname = github_config.get("hostname", "")
        
        self._headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Engineering Flow Platform-Mini",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"
        
        logger.info("GitHubChannel reinitialized")
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make an API request with rate limit handling and exponential backoff."""
        url = f"{self.base_url}{endpoint}"
        
        # Debug: Log request
        if _is_debug_enabled():
            logger.debug(f"=== [GITHUB] REQUEST ===")
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
                if _is_debug_enabled():
                    logger.debug(f"=== [GITHUB] RESPONSE ===")
                    logger.debug(f"Status: {response.status_code}")
                
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
                if _is_debug_enabled():
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
        logger.info(f"Creating issue in {owner}/{repo}: {truncate(title, 50)}")
        
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
            sha: SHA of file being updated (optional, will auto-fetch if not provided)
            branch: Branch name
            
        Returns:
            Commit data
        """
        import base64
        
        # Auto-fetch SHA if not provided (required for updating existing files)
        if not sha:
            try:
                existing = await self.get_file(owner, repo, path, branch or "main")
                if existing:
                    sha = existing.get("sha")
                    logger.info(f"Auto-fetched SHA for {path}: {sha[:7] if sha else 'None'}")
            except Exception as e:
                logger.debug(f"Could not fetch existing file SHA: {e}")
        
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
    
    async def get_pr_files(self, owner: str, repo: str, pull_number: int) -> Dict[str, Any]:
        """Get files changed in a pull request."""
        logger.info(f"Getting PR files {owner}/{repo}#{pull_number}")
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/files"
        )
    
    async def get_pr_diff(self, owner: str, repo: str, pull_number: int) -> Dict[str, Any]:
        """Get the diff of a pull request."""
        logger.info(f"Getting PR diff {owner}/{repo}#{pull_number}")
        
        # Use Accept header to request diff format
        headers = {**self._headers, "Accept": "application/vnd.github.v3.diff"}
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pull_number}"
        response = await self.client.get(url, headers=headers)
        
        if response.status_code >= 400:
            raise Exception(f"GitHub API error: {response.status_code}")
        
        # Response is now raw diff text
        return {"diff": response.text}
    
    async def get_pr_comments(self, owner: str, repo: str, pull_number: int) -> Dict[str, Any]:
        """Get review comments on a pull request."""
        logger.info(f"Getting PR comments {owner}/{repo}#{pull_number}")
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/comments"
        )
    
    async def add_pr_review_comment(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        body: str,
        commit_id: Optional[str] = None,
        path: Optional[str] = None,
        line: Optional[int] = None
    ) -> Dict[str, Any]:
        """Add a review comment to a pull request."""
        logger.info(f"Adding PR review comment {owner}/{repo}#{pull_number}")
        
        if path and line:
            # For inline comments, we need a real commit SHA
            commit_id_to_use = commit_id
            if not commit_id_to_use:
                pr = await self._request(
                    "GET",
                    f"/repos/{owner}/{repo}/pulls/{pull_number}",
                )
                commit_id_to_use = pr.get("head", {}).get("sha")
                if not commit_id_to_use:
                    raise ValueError("Unable to determine commit SHA for PR review comment")
            
            return await self._request(
                "POST",
                f"/repos/{owner}/{repo}/pulls/{pull_number}/comments",
                json={
                    "body": body,
                    "commit_id": commit_id_to_use,
                    "path": path,
                    "line": line,
                    "side": "RIGHT"
                }
            )
        else:
            return await self._request(
                "POST",
                f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
                json={
                    "body": body,
                    "event": "COMMENT"
                }
            )
    
    async def list_pr_reviews(self, owner: str, repo: str, pull_number: int) -> Dict[str, Any]:
        """List all reviews on a pull request."""
        logger.info(f"Listing PR reviews {owner}/{repo}#{pull_number}")
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        )
    
    async def list_branches(self, owner: str, repo: str) -> Dict[str, Any]:
        """List branches in a repository."""
        logger.info(f"Listing branches {owner}/{repo}")
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/branches"
        )
    
    async def get_repo(self, owner: str, repo: str) -> Dict[str, Any]:
        """Get repository information."""
        logger.info(f"Getting repo {owner}/{repo}")
        return await self._request(
            "GET", f"/repos/{owner}/{repo}"
        )
    
    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        from_branch: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new branch."""
        if not from_branch:
            repo_info = await self.get_repo(owner, repo)
            from_branch = repo_info.get("default_branch", "main")
        
        ref_response = await self._request(
            "GET", f"/repos/{owner}/{repo}/git/refs/heads/{from_branch}"
        )
        sha = ref_response.get("object", {}).get("sha")
        
        if not sha:
            raise Exception(f"Could not get SHA for branch {from_branch}")
        
        logger.info(f"Creating branch {branch_name} from {from_branch}")
        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={
                "ref": f"refs/heads/{branch_name}",
                "sha": sha
            }
        )
    
    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str = "main"
    ) -> Dict[str, Any]:
        """Create a new pull request."""
        logger.info(f"Creating PR {owner}/{repo}: {head} -> {base}")
        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base
            }
        )
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


# Global instance
github_channel = GitHubChannel()

# Register for config reload
from src.config import service_reload_manager
service_reload_manager.register('github', github_channel.reinit)


# ========== Tool Functions ==========

async def github_get_issue(owner: str, repo: str, issue_number: int) -> str:
    """Get an issue or PR details."""
    try:
        issue = await github_channel._request(
            "GET", f"/repos/{owner}/{repo}/issues/{issue_number}"
        )
        
        state = issue.get("state", "unknown")
        title = issue.get("title", "Untitled")
        body = issue.get("body", "")
        
        return f"**{owner}/{repo}#{issue_number}: {title}**\n\n**State:** {state}\n\n{body}"
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
            title = item.get("title", "")
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


async def github_get_pr_files(owner: str, repo: str, pull_number: int) -> str:
    """Get list of files changed in a PR."""
    try:
        result = await github_channel.get_pr_files(owner, repo, pull_number)
        files = result if isinstance(result, list) else result.get("files", [])
        
        if not files:
            return f"No files changed in PR #{pull_number}"
        
        lines = [f"**Files Changed** ({len(files)}):\n"]
        for f in files:
            status = f.get("status", "modified")
            additions = f.get("additions", 0)
            deletions = f.get("deletions", 0)
            lines.append(f"- `{f.get('filename', '')}` [{status}] +{additions} -{deletions}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting PR files: {e}"


async def github_get_pr_diff(owner: str, repo: str, pull_number: int) -> str:
    """Get the diff of a PR."""
    try:
        result = await github_channel.get_pr_diff(owner, repo, pull_number)
        diff = result.get("diff", "") if isinstance(result, dict) else str(result)
        
        if not diff:
            return f"No diff available for PR #{pull_number}"
        
        if len(diff) > 50000:
            diff = diff[:50000] + f"\n\n... (truncated, total {len(diff)} chars)"
        
        return f"**PR #{pull_number} Diff:**\n\n{diff}"
    except Exception as e:
        return f"Error getting PR diff: {e}"


async def github_get_pr_comments(owner: str, repo: str, pull_number: int) -> str:
    """Get review comments on a PR."""
    try:
        result = await github_channel.get_pr_comments(owner, repo, pull_number)
        comments = result if isinstance(result, list) else result.get("comments", [])
        
        if not comments:
            return f"No comments on PR #{pull_number}"
        
        lines = [f"**Review Comments** ({len(comments)}):\n"]
        for c in comments:
            user = c.get("user", {}).get("login", "unknown")
            body = c.get("body", "")[:200]
            path = c.get("path", "")
            line = c.get("line", c.get("original_line", ""))
            lines.append(f"- **{user}** at `{path}:{line}`: {body}...")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting PR comments: {e}"


async def github_add_pr_review_comment(
    owner: str,
    repo: str,
    pull_number: int,
    body: str,
    commit_id: Optional[str] = None,
    path: Optional[str] = None,
    line: Optional[int] = None
) -> str:
    """Add a review comment to a PR."""
    try:
        result = await github_channel.add_pr_review_comment(
            owner, repo, pull_number, body, commit_id, path, line
        )
        return f"Review comment added to PR #{pull_number}"
    except Exception as e:
        return f"Error adding review comment: {e}"


async def github_list_pr_reviews(owner: str, repo: str, pull_number: int) -> str:
    """List all reviews on a PR."""
    try:
        result = await github_channel.list_pr_reviews(owner, repo, pull_number)
        reviews = result if isinstance(result, list) else result.get("reviews", [])
        
        if not reviews:
            return f"No reviews on PR #{pull_number}"
        
        lines = [f"**Reviews** ({len(reviews)}):\n"]
        for r in reviews:
            user = r.get("user", {}).get("login", "unknown")
            state = r.get("state", "")
            lines.append(f"- **{user}**: {state}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing PR reviews: {e}"


async def github_list_branches(owner: str, repo: str) -> str:
    """List branches in a repository."""
    try:
        result = await github_channel.list_branches(owner, repo)
        branches = result if isinstance(result, list) else result.get("branches", [])
        
        if not branches:
            return f"No branches found in {owner}/{repo}"
        
        lines = [f"**Branches** ({len(branches)}):\n"]
        for b in branches[:20]:
            name = b.get("name", "")
            protected = "🔒" if b.get("protected") else ""
            lines.append(f"- {name} {protected}")
        
        if len(branches) > 20:
            lines.append(f"\n... and {len(branches) - 20} more")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing branches: {e}"


async def github_get_default_branch(owner: str, repo: str) -> str:
    """Get the default branch of a repository."""
    try:
        result = await github_channel.get_repo(owner, repo)
        default_branch = result.get("default_branch", "main")
        return f"Default branch for {owner}/{repo}: **{default_branch}**"
    except Exception as e:
        return f"Error getting default branch: {e}"


async def github_create_branch(owner: str, repo: str, branch_name: str, from_branch: Optional[str] = None) -> str:
    """Create a new branch."""
    try:
        result = await github_channel.create_branch(owner, repo, branch_name, from_branch)
        return f"Branch `{branch_name}` created in {owner}/{repo}"
    except Exception as e:
        return f"Error creating branch: {e}"


async def github_get_file_content(owner: str, repo: str, path: str, branch: Optional[str] = None) -> str:
    """Get file content from a repository."""
    try:
        result = await github_channel.get_file(owner, repo, path, branch)
        content = result.get("content", "")
        if content:
            import base64
            decoded = base64.b64decode(content).decode("utf-8")
            if len(decoded) > 10000:
                decoded = decoded[:10000] + "\n\n... (truncated)"
            return f"**File:** {owner}/{repo}/{path}\n\n```\n{decoded}\n```"
        return f"No content found for {path}"
    except Exception as e:
        return f"Error getting file: {e}"


async def github_create_pull_request(
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str = "main"
) -> str:
    """Create a new pull request."""
    try:
        result = await github_channel.create_pull_request(owner, repo, title, body, head, base)
        pr_url = result.get("html_url", "")
        pr_number = result.get("number", "")
        return f"PR created: **{title}** (#{pr_number})\n{pr_url}"
    except Exception as e:
        return f"Error creating PR: {e}"


async def github_create_or_update_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    sha: Optional[str] = None,
    branch: str = ""
) -> str:
    """Create or update a file in a repository."""
    try:
        result = await github_channel.create_or_update_file(
            owner, repo, path, content, message, sha, branch
        )
        commit = result.get("commit", {})
        return f"File {path} updated in {owner}/{repo}\nCommit: {commit.get('sha', 'unknown')[:7]}"
    except Exception as e:
        return f"Error updating file: {e}"
