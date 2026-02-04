"""
GitHub Skill - Interact with GitHub using gh CLI.

Provides tools for cloning repositories, managing issues/PRs, and running workflows.

Supports configurable hostname for GitHub Enterprise instances.
Configuration: github.hostname in config.yaml (default: github.com)
"""

import asyncio
from pathlib import Path
from typing import Optional

from skills.executor import SkillResult, skill

# Default to github.com, can be overridden via config or arguments
DEFAULT_HOSTNAME = "github.com"


def _get_hostname() -> str:
    """Get configured GitHub hostname from config."""
    try:
        from config import config
        return config.get("github.hostname", DEFAULT_HOSTNAME)
    except Exception:
        return DEFAULT_HOSTNAME


async def _run_gh_command(args: list, cwd: str = None, hostname: str = None) -> tuple:
    """Run a gh command and return (success, output)."""
    # Add hostname flag if specified or if not github.com
    if hostname and hostname != DEFAULT_HOSTNAME:
        args = ["--hostname", hostname] + args
    elif hostname is None:
        # Use configured hostname
        configured = _get_hostname()
        if configured != DEFAULT_HOSTNAME:
            args = ["--hostname", configured] + args
    
    try:
        result = await asyncio.create_subprocess_exec(
            "gh", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd or str(Path.home())
        )
        stdout, _ = await result.communicate()
        return result.returncode == 0, stdout.decode("utf-8").strip()
    except FileNotFoundError:
        return False, "Error: 'gh' CLI not found. Install with: brew install gh (macOS) or apt install gh (Linux)"
    except Exception as e:
        return False, f"Error: {e}"


async def github_clone(repo: str, directory: str = None, branch: str = None, 
                       hostname: str = None) -> SkillResult:
    """Clone a GitHub repository.
    
    Args:
        repo: Repository in 'owner/repo' format
        directory: Target directory (optional)
        branch: Branch to clone (optional)
        hostname: GitHub hostname (for GitHub Enterprise)
    """
    args = ["repo", "clone", repo]
    if branch:
        args.extend(["--branch", branch])
    if directory:
        args.append(directory)
    
    success, output = await _run_gh_command(args, hostname=hostname)
    
    if success:
        return SkillResult(success=True, output=f"✅ Cloned {repo}")
    return SkillResult(success=False, error=output)


async def github_repo_clone(owner: str = None, repo: str = None, 
                           select: bool = False, hostname: str = None) -> SkillResult:
    """Clone a repository with interactive selection or specified owner/repo.
    
    Args:
        owner: Repository owner
        repo: Repository name
        select: Use interactive selection
        hostname: GitHub hostname (for GitHub Enterprise)
    """
    if select:
        success, output = await _run_gh_command(["repo", "clone", "--select", "--web"], hostname=hostname)
        if success:
            return SkillResult(success=True, output="✅ Opened GitHub for repository selection")
        return SkillResult(success=False, error=output)
    
    if owner and repo:
        success, output = await _run_gh_command(["repo", "clone", f"{owner}/{repo}"], hostname=hostname)
        if success:
            return SkillResult(success=True, output=f"✅ Cloned {owner}/{repo}")
        return SkillResult(success=False, error=output)
    
    return SkillResult(success=False, error="Please specify owner/repo or use select=true")


async def github_issue_list(owner: str, repo: str, state: str = "open", 
                           limit: int = 10, hostname: str = None) -> SkillResult:
    """List issues in a repository.
    
    Args:
        owner: Repository owner
        repo: Repository name
        state: Issue state (open, closed, all)
        limit: Maximum results
        hostname: GitHub hostname (for GitHub Enterprise)
    """
    success, output = await _run_gh_command([
        "issue", "list",
        "--repo", f"{owner}/{repo}",
        "--state", state,
        "--limit", str(limit)
    ], hostname=hostname)
    
    if success:
        lines = output.split("\n") if output else []
        if lines and lines[0]:
            result = [f"**Issues** ({state})\n"]
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 3:
                    num, title, labels = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
                    result.append(f"- #{num} {title} {labels}")
            return SkillResult(success=True, output="\n".join(result))
        return SkillResult(success=True, output=f"No {state} issues found")
    return SkillResult(success=False, error=output)


async def github_pr_list(owner: str, repo: str, state: str = "open", 
                         limit: int = 10, hostname: str = None) -> SkillResult:
    """List pull requests in a repository.
    
    Args:
        owner: Repository owner
        repo: Repository name
        state: PR state (open, closed, all)
        limit: Maximum results
        hostname: GitHub hostname (for GitHub Enterprise)
    """
    success, output = await _run_gh_command([
        "pr", "list",
        "--repo", f"{owner}/{repo}",
        "--state", state,
        "--limit", str(limit)
    ], hostname=hostname)
    
    if success:
        lines = output.split("\n") if output else []
        if lines and lines[0]:
            result = [f"**Pull Requests** ({state})\n"]
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 3:
                    num, title, state_pr = parts[0], parts[1], parts[2]
                    result.append(f"- #{num} {title} [{state_pr}]")
            return SkillResult(success=True, output="\n".join(result))
        return SkillResult(success=True, output=f"No {state} PRs found")
    return SkillResult(success=False, error=output)


async def github_pr_checks(owner: str, repo: str, pr_number: int, 
                           hostname: str = None) -> SkillResult:
    """Check CI status on a PR.
    
    Args:
        owner: Repository owner
        repo: Repository name
        pr_number: PR number
        hostname: GitHub hostname (for GitHub Enterprise)
    """
    success, output = await _run_gh_command([
        "pr", "checks",
        "--repo", f"{owner}/{repo}",
        str(pr_number)
    ], hostname=hostname)
    
    if success:
        return SkillResult(success=True, output=f"**PR #{pr_number} Checks**\n\n{output}")
    return SkillResult(success=False, error=output)


async def github_run_list(owner: str, repo: str, limit: int = 10, 
                          hostname: str = None) -> SkillResult:
    """List recent workflow runs.
    
    Args:
        owner: Repository owner
        repo: Repository name
        limit: Maximum results
        hostname: GitHub hostname (for GitHub Enterprise)
    """
    success, output = await _run_gh_command([
        "run", "list",
        "--repo", f"{owner}/{repo}",
        "--limit", str(limit)
    ], hostname=hostname)
    
    if success:
        return SkillResult(success=True, output=f"**Workflow Runs**\n\n{output}")
    return SkillResult(success=False, error=output)


async def github_run_view(owner: str, repo: str, run_id: str, 
                          log_failed: bool = False, hostname: str = None) -> SkillResult:
    """View a workflow run.
    
    Args:
        owner: Repository owner
        repo: Repository name
        run_id: Run ID or number
        log_failed: Show only failed steps
        hostname: GitHub hostname (for GitHub Enterprise)
    """
    args = ["run", "view", "--repo", f"{owner}/{repo}", run_id]
    if log_failed:
        args.append("--log-failed")
    
    success, output = await _run_gh_command(args, hostname=hostname)
    
    if success:
        return SkillResult(success=True, output=f"**Run {run_id}**\n\n{output}")
    return SkillResult(success=False, error=output)


async def github_api(endpoint: str, method: str = "GET", 
                     body: str = None, hostname: str = None) -> SkillResult:
    """Make an arbitrary GitHub API call.
    
    Args:
        endpoint: API endpoint (e.g., 'repos/owner/repo')
        method: HTTP method
        body: Request body (JSON)
        hostname: GitHub hostname (for GitHub Enterprise)
    """
    args = ["api", endpoint, "--method", method]
    if body:
        args.extend(["--input", "-"])
    
    success, output = await _run_gh_command(args, hostname=hostname)
    
    if success:
        return SkillResult(success=True, output=f"**API Response**\n\n{output}")
    return SkillResult(success=False, error=output)


@skill(
    name="github",
    description="Interact with GitHub using gh CLI. Commands: clone, repo_clone, issue_list, pr_list, pr_checks, run_list, run_view, api"
)
async def github(
    command: str = "clone",
    repo: str = None,
    owner: str = None,
    directory: str = None,
    branch: str = None,
    state: str = "open",
    limit: int = 10,
    pr_number: int = None,
    run_id: str = None,
    endpoint: str = None,
    method: str = "GET",
    body: str = None,
    select: bool = False,
    hostname: str = None
) -> SkillResult:
    """Execute GitHub commands using gh CLI.
    
    Args:
        command: Sub-command (clone, repo_clone, issue_list, pr_list, pr_checks, run_list, run_view, api)
        repo: Repository (owner/repo format)
        owner: Repository owner
        directory: Target directory
        branch: Branch name
        state: Issue/PR state (open, closed, all)
        limit: Maximum results
        pr_number: Pull request number
        run_id: Workflow run ID
        endpoint: API endpoint
        method: HTTP method
        body: Request body
        select: Use interactive selection
        hostname: GitHub hostname (for GitHub Enterprise)
    """
    cmd = command.lower().replace("_", "-")
    
    if cmd in ("clone", "repo-clone"):
        return await github_repo_clone(owner=owner, repo=repo, directory=directory, branch=branch, hostname=hostname, select=select)
    elif cmd == "issue-list":
        if not owner or not repo:
            return SkillResult(success=False, error="owner and repo required for issue_list")
        return await github_issue_list(owner, repo, state=state, limit=limit, hostname=hostname)
    elif cmd in ("pr-list", "pr_list"):
        if not owner or not repo:
            return SkillResult(success=False, error="owner and repo required for pr_list")
        return await github_pr_list(owner, repo, state=state, limit=limit, hostname=hostname)
    elif cmd == "pr-checks":
        if not owner or not repo or not pr_number:
            return SkillResult(success=False, error="owner, repo, and pr_number required for pr_checks")
        return await github_pr_checks(owner, repo, pr_number=pr_number, hostname=hostname)
    elif cmd in ("run-list", "run_list"):
        if not owner or not repo:
            return SkillResult(success=False, error="owner and repo required for run_list")
        return await github_run_list(owner, repo, limit=limit, hostname=hostname)
    elif cmd in ("run-view", "run_view"):
        if not owner or not repo or not run_id:
            return SkillResult(success=False, error="owner, repo, and run_id required for run_view")
        return await github_run_view(owner, repo, run_id=run_id, hostname=hostname)
    elif cmd == "api":
        return await github_api(endpoint=endpoint, method=method, body=body, hostname=hostname)
    else:
        return SkillResult(success=False, error=f"Unknown github command: {command}. Available: clone, repo_clone, issue_list, pr_list, pr_checks, run_list, run_view, api")
