"""
GitHub Skill - Interact with GitHub using gh CLI.

Provides tools for cloning repositories, managing issues/PRs, and running workflows.
"""

import asyncio
from pathlib import Path
from typing import Optional

from skills.executor import SkillResult


async def _run_gh_command(args: list, cwd: str = None) -> tuple:
    """Run a gh command and return (success, output)."""
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


async def github_clone(repo: str, directory: str = None, branch: str = None) -> SkillResult:
    """Clone a GitHub repository.
    
    Args:
        repo: Repository in 'owner/repo' format
        directory: Target directory (optional)
        branch: Branch to clone (optional)
    """
    args = ["repo", "clone", repo]
    if branch:
        args.extend(["--branch", branch])
    if directory:
        args.append(directory)
    
    success, output = await _run_gh_command(args)
    
    if success:
        return SkillResult(success=True, output=f"✅ Cloned {repo}")
    return SkillResult(success=False, error=output)


async def github_repo_clone(owner: str = None, repo: str = None, 
                           select: bool = False) -> SkillResult:
    """Clone a repository with interactive selection or specified owner/repo.
    
    Args:
        owner: Repository owner
        repo: Repository name
        select: Use interactive selection
    """
    if select:
        success, output = await _run_gh_command(["repo", "clone", "--select", "--web"])
        if success:
            return SkillResult(success=True, output="✅ Opened GitHub for repository selection")
        return SkillResult(success=False, error=output)
    
    if owner and repo:
        success, output = await _run_gh_command(["repo", "clone", f"{owner}/{repo}"])
        if success:
            return SkillResult(success=True, output=f"✅ Cloned {owner}/{repo}")
        return SkillResult(success=False, error=output)
    
    return SkillResult(success=False, error="Please specify owner/repo or use select=true")


async def github_issue_list(owner: str, repo: str, state: str = "open", 
                           limit: int = 10) -> SkillResult:
    """List issues in a repository.
    
    Args:
        owner: Repository owner
        repo: Repository name
        state: Issue state (open, closed, all)
        limit: Maximum results
    """
    success, output = await _run_gh_command([
        "issue", "list",
        "--repo", f"{owner}/{repo}",
        "--state", state,
        "--limit", str(limit)
    ])
    
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
                         limit: int = 10) -> SkillResult:
    """List pull requests in a repository.
    
    Args:
        owner: Repository owner
        repo: Repository name
        state: PR state (open, closed, all)
        limit: Maximum results
    """
    success, output = await _run_gh_command([
        "pr", "list",
        "--repo", f"{owner}/{repo}",
        "--state", state,
        "--limit", str(limit)
    ])
    
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


async def github_pr_checks(owner: str, repo: str, pr_number: int) -> SkillResult:
    """Check CI status on a PR.
    
    Args:
        owner: Repository owner
        repo: Repository name
        pr_number: PR number
    """
    success, output = await _run_gh_command([
        "pr", "checks",
        "--repo", f"{owner}/{repo}",
        str(pr_number)
    ])
    
    if success:
        return SkillResult(success=True, output=f"**PR #{pr_number} Checks**\n\n{output}")
    return SkillResult(success=False, error=output)


async def github_run_list(owner: str, repo: str, limit: int = 10) -> SkillResult:
    """List recent workflow runs.
    
    Args:
        owner: Repository owner
        repo: Repository name
        limit: Maximum results
    """
    success, output = await _run_gh_command([
        "run", "list",
        "--repo", f"{owner}/{repo}",
        "--limit", str(limit)
    ])
    
    if success:
        return SkillResult(success=True, output=f"**Workflow Runs**\n\n{output}")
    return SkillResult(success=False, error=output)


async def github_run_view(owner: str, repo: str, run_id: str, 
                          log_failed: bool = False) -> SkillResult:
    """View a workflow run.
    
    Args:
        owner: Repository owner
        repo: Repository name
        run_id: Run ID or number
        log_failed: Show only failed steps
    """
    args = ["run", "view", "--repo", f"{owner}/{repo}", run_id]
    if log_failed:
        args.append("--log-failed")
    
    success, output = await _run_gh_command(args)
    
    if success:
        return SkillResult(success=True, output=f"**Run {run_id}**\n\n{output}")
    return SkillResult(success=False, error=output)


async def github_api(endpoint: str, method: str = "GET", 
                     body: str = None) -> SkillResult:
    """Make an arbitrary GitHub API call.
    
    Args:
        endpoint: API endpoint (e.g., 'repos/owner/repo')
        method: HTTP method
        body: Request body (JSON)
    """
    args = ["api", endpoint, "--method", method]
    if body:
        args.extend(["--input", "-"])
    
    success, output = await _run_gh_command(args)
    
    if success:
        return SkillResult(success=True, output=f"**API Response**\n\n{output}")
    return SkillResult(success=False, error=output)
