"""GitHub Integration - Single source of truth for GitHub operations."""

from typing import Optional

from .api import (
    GitHubChannel as GitHubClient,
    github_channel,
    github_get_issue,
    github_search_issues,
    github_add_comment,
    github_get_pr_files,
    github_get_pr_diff,
    github_get_pr_comments,
    github_add_pr_review_comment,
    github_list_pr_reviews,
    github_list_branches,
    github_get_default_branch,
    github_create_branch,
    github_get_file_content,
    github_create_pull_request,
)

__all__ = [
    "GitHubClient",
    "github_channel",
    "github_get_issue",
    "github_search_issues",
    "github_add_comment",
    "github_get_pr_files",
    "github_get_pr_diff",
    "github_get_pr_comments",
    "github_add_pr_review_comment",
    "github_list_pr_reviews",
    "github_list_branches",
    "github_get_default_branch",
    "github_create_branch",
    "github_get_file_content",
    "github_create_pull_request",
    "get_tools_schemas",
]


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
            title = item.get("title", "")
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
                        "owner": {"type": "string", "description": "Repository owner (e.g., 'myorg')"},
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
        {
            "type": "function",
            "function": {
                "name": "github_get_pr_files",
                "description": "Get list of files changed in a PR",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "pull_number": {"type": "integer", "description": "PR number"}
                    },
                    "required": ["owner", "repo", "pull_number"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_get_pr_diff",
                "description": "Get the diff of a PR",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "pull_number": {"type": "integer", "description": "PR number"}
                    },
                    "required": ["owner", "repo", "pull_number"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_get_pr_comments",
                "description": "Get review comments on a PR",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "pull_number": {"type": "integer", "description": "PR number"}
                    },
                    "required": ["owner", "repo", "pull_number"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_add_pr_review_comment",
                "description": "Add a review comment to a PR (can specify file path and line number)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "pull_number": {"type": "integer", "description": "PR number"},
                        "body": {"type": "string", "description": "Comment text"},
                        "commit_id": {"type": "string", "description": "Commit SHA (optional)"},
                        "path": {"type": "string", "description": "File path for line comment (optional)"},
                        "line": {"type": "integer", "description": "Line number for line comment (optional)"}
                    },
                    "required": ["owner", "repo", "pull_number", "body"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_list_pr_reviews",
                "description": "List all reviews on a PR",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "pull_number": {"type": "integer", "description": "PR number"}
                    },
                    "required": ["owner", "repo", "pull_number"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_list_branches",
                "description": "List branches in a repository",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"}
                    },
                    "required": ["owner", "repo"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_get_default_branch",
                "description": "Get the default branch of a repository",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"}
                    },
                    "required": ["owner", "repo"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_create_branch",
                "description": "Create a new branch",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "branch_name": {"type": "string", "description": "New branch name"},
                        "from_branch": {"type": "string", "description": "Base branch to create from (optional)"}
                    },
                    "required": ["owner", "repo", "branch_name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_get_file_content",
                "description": "Get file content from a repository",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "path": {"type": "string", "description": "File path"},
                        "branch": {"type": "string", "description": "Branch name (optional)"}
                    },
                    "required": ["owner", "repo", "path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "github_create_pull_request",
                "description": "Create a new pull request",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "description": "Repository owner"},
                        "repo": {"type": "string", "description": "Repository name"},
                        "title": {"type": "string", "description": "PR title"},
                        "body": {"type": "string", "description": "PR description"},
                        "head": {"type": "string", "description": "Source branch"},
                        "base": {"type": "string", "description": "Target branch (default: main)"}
                    },
                    "required": ["owner", "repo", "title", "body", "head"]
                }
            }
        },
    ]
