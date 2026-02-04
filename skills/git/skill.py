"""
Git Skill - Local Git Management for AI.

Provides structured git operations for managing local repositories.

Usage:
- git status → Check working tree status
- git commit → Stage and commit changes
- git push → Push to remote
- git pull → Pull from remote
- git branch → List/create branches
- git log → Show commit history
- git checkout → Switch branches
- git diff → Show unstaged changes
- git add → Stage files
"""

import asyncio
from typing import Optional

from skills.executor import skill


async def _run_git_command(args: list, cwd: str = None) -> str:
    """Run a git command and return output."""
    try:
        result = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd or "/root/.openclaw/workspace/codew"
        )
        stdout, _ = await result.communicate()
        return stdout.decode("utf-8").strip()
    except Exception as e:
        return f"Error: {e}"


@skill(
    name="git",
    description="Manage local git repositories. Commands: status, commit, push, pull, branch, log, checkout, diff, add"
)
async def git(command: str = "status", message: str = None, branch: str = None, 
              path: str = ".", delete: bool = False, limit: int = 10,
              repo_path: str = None) -> str:
    """Execute git commands for local repository management.
    
    Args:
        command: Git command (status, commit, push, pull, branch, log, checkout, diff, add)
        message: Commit message (for commit command)
        branch: Branch name (for push, branch, checkout commands)
        path: File path (for add, diff commands)
        delete: Delete branch (for branch command)
        limit: Limit number of commits (for log command)
        repo_path: Repository path (optional)
    """
    cmd = command.lower()
    
    if cmd == "status":
        return await _status(repo_path)
    elif cmd == "commit":
        return await _commit(message, repo_path)
    elif cmd == "push":
        return await _push(branch, repo_path)
    elif cmd == "pull":
        return await _pull(repo_path)
    elif cmd == "branch":
        return await _branch(branch, delete, repo_path)
    elif cmd == "log":
        return await _log(limit, repo_path)
    elif cmd == "checkout":
        return await _checkout(branch, repo_path)
    elif cmd == "diff":
        return await _diff(path, repo_path)
    elif cmd == "add":
        return await _add(path, repo_path)
    else:
        return f"Unknown git command: {command}. Available: status, commit, push, pull, branch, log, checkout, diff, add"


async def _status(repo_path: str = None) -> str:
    """Show working tree status."""
    output = await _run_git_command(["status", "--porcelain"], repo_path)
    if not output:
        return "✅ Working tree is clean"
    
    lines = output.split("\n")
    staged = [l for l in lines if l.startswith("A ") or l.startswith("M ")]
    unstaged = [l for l in lines if l.startswith(" M") or l.startswith("??")]
    
    result = ["**Git Status**"]
    if staged:
        result.append(f"\n📦 **Staged ({len(staged)}):**")
        for s in staged:
            result.append(f"  {s}")
    if unstaged:
        result.append(f"\n📝 **Unstaged ({len(unstaged)}):**")
        for s in unstaged:
            result.append(f"  {s}")
    
    return "\n".join(result)


async def _commit(message: str, repo_path: str = None) -> str:
    """Commit staged changes."""
    if not message:
        return "Error: Commit message required"
    
    await _run_git_command(["add", "-A"], repo_path)
    status = await _run_git_command(["status", "--porcelain"], repo_path)
    
    if not status.strip():
        return "Nothing to commit - working tree is clean"
    
    output = await _run_git_command(["commit", "-m", message], repo_path)
    
    if "Error" in output:
        return f"❌ {output}"
    
    return f"✅ Committed successfully\n\n{message}"


async def _push(branch: str, repo_path: str = None) -> str:
    """Push to remote."""
    branch = branch or "main"
    output = await _run_git_command(["push", "origin", branch], repo_path)
    
    if "Error" in output:
        return f"❌ Push failed: {output}"
    if "up to date" in output.lower() or "everything up-to-date" in output.lower():
        return "✅ Already up to date"
    
    return f"✅ Pushed to {branch}"


async def _pull(repo_path: str = None) -> str:
    """Pull from remote."""
    output = await _run_git_command(["pull"], repo_path)
    
    if "Error" in output:
        return f"❌ Pull failed: {output}"
    if "Already up to date" in output:
        return "✅ Already up to date"
    
    return f"✅ Pulled changes\n\n{output[:200]}"


async def _branch(name: str, delete: bool, repo_path: str = None) -> str:
    """List, create, or delete branches."""
    if delete and name:
        await _run_git_command(["branch", "-D", name], repo_path)
        return f"✅ Deleted branch {name}"
    
    if name:
        await _run_git_command(["checkout", "-b", name], repo_path)
        return f"✅ Created and switched to branch '{name}'"
    
    # List branches
    output = await _run_git_command(["branch", "-a"], repo_path)
    lines = output.split("\n")
    
    result = ["**Branches**"]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("*"):
            result.append(f"  🔹 *{line[1:].strip()}** (current)")
        elif line.startswith("remotes/"):
            result.append(f"  📌 {line}")
        else:
            result.append(f"    {line}")
    
    return "\n".join(result)


async def _log(limit: int, repo_path: str = None) -> str:
    """Show commit history."""
    output = await _run_git_command(
        ["log", "--oneline", f"-n{limit}", "--decorate"],
        repo_path
    )
    
    if not output:
        return "No commit history"
    
    lines = output.split("\n")
    result = [f"**Recent Commits** ({len(lines)}):\n"]
    for line in lines:
        if line.strip():
            result.append(f"• {line}")
    
    return "\n".join(result)


async def _checkout(branch: str, repo_path: str = None) -> str:
    """Switch to a branch."""
    if not branch:
        return "Error: Branch name required"
    
    output = await _run_git_command(["checkout", branch], repo_path)
    
    if "Error" in output:
        return f"❌ Checkout failed: {output}"
    
    return f"✅ Switched to branch '{branch}'"


async def _diff(path: str, repo_path: str = None) -> str:
    """Show unstaged changes."""
    output = await _run_git_command(["diff", "--stat"], repo_path)
    
    if not output or output == "":
        return "No unstaged changes"
    
    return f"**Unstaged Changes**\n\n{output}"


async def _add(path: str, repo_path: str = None) -> str:
    """Stage files for commit."""
    await _run_git_command(["add", path], repo_path)
    return f"✅ Staged {path}"
