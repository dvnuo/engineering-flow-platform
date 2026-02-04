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
- git clone → Clone repositories
"""

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from skills.executor import SkillResult, skill
from config import config

logger = logging.getLogger(__name__)

# Default workspace path using home directory
DEFAULT_WORKSPACE = Path.home() / ".opsclaw" / "workspace"

# Flag to track if SSH key has been setup (prevent repeated initialization)
_ssh_key_setup_done = False


async def _setup_ssh_key() -> bool:
    """Setup SSH key from config.
    
    Copies configured private key to ~/.ssh/ and sets proper permissions.
    Also handles known_hosts for automatic host verification.
    Only runs once per process lifetime to avoid repeated initialization.
    """
    global _ssh_key_setup_done
    
    # Skip if already setup
    if _ssh_key_setup_done:
        return True
    
    ssh_config = config.get("ssh", {})
    if not ssh_config.get("enabled", False):
        return False
    
    private_key_path = ssh_config.get("private_key_path", "")
    if not private_key_path:
        logger.debug("SSH enabled but no private_key_path configured")
        return False
    
    source_path = Path(private_key_path)
    if not source_path.exists():
        logger.debug(f"SSH key not found at {private_key_path}")
        return False
    
    # Ensure ~/.ssh directory exists
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy key to ~/.ssh/
    dest_path = ssh_dir / source_path.name
    try:
        shutil.copy2(source_path, dest_path)
        logger.info(f"Copied SSH key to {dest_path}")
    except Exception as e:
        logger.debug(f"Failed to copy SSH key: {e}")
        return False
    
    # Set permissions to 600 (required by SSH)
    try:
        os.chmod(dest_path, 0o600)
        logger.info(f"Set SSH key permissions to 600")
    except Exception as e:
        logger.debug(f"Failed to set SSH key permissions: {e}")
        return False
    
    # Setup known_hosts for automatic host verification
    await _setup_known_hosts(ssh_dir)
    
    # Mark as done
    _ssh_key_setup_done = True
    return True


async def _setup_known_hosts(ssh_dir: Path) -> None:
    """Add Git host to known_hosts for automatic trust.
    
    Uses github.base_url from config to determine the host.
    Runs keyscan asynchronously for better performance.
    """
    global _ssh_key_setup_done
    
    known_hosts_file = ssh_dir / "known_hosts"
    
    # Check if already configured (skip if file exists with content)
    if known_hosts_file.exists() and known_hosts_file.stat().st_size > 0:
        return
    
    # Get GitHub base URL from config
    github_config = config.get("github", {})
    base_url = github_config.get("base_url", "https://api.github.com")
    
    # Extract hostname from URL
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    github_host = parsed.netloc if parsed.netloc else "github.com"
    
    # Remove port if present
    if ":" in github_host:
        github_host = github_host.split(":")[0]
    
    logger.info(f"Setting up known_hosts for {github_host}")
    
    hosts_to_scan = [github_host]
    
    # Also add github.com for API access if using public GitHub
    if "api.github.com" in base_url:
        hosts_to_scan.append("github.com")
    
    # Open file for appending
    try:
        with open(str(known_hosts_file), "a") as kh:
            for host in hosts_to_scan:
                try:
                    # Use asyncio subprocess for keyscan
                    proc = await asyncio.create_subprocess_exec(
                        "ssh-keyscan", "-H", "-t", "rsa,ecdsa,ed25519", host,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                    stdout, _ = await proc.communicate()
                    
                    if stdout:
                        kh.write(stdout.decode("utf-8"))
                        logger.debug(f"Added {host} to known_hosts")
                        
                except Exception as e:
                    # Fail quietly - host may be unreachable during setup
                    logger.debug(f"Could not add {host} to known_hosts: {e}")
        
        # Set permissions on known_hosts
        os.chmod(str(known_hosts_file), 0o644)
        logger.info("Known hosts file configured")
        
    except Exception as e:
        # Fail quietly - known_hosts is optional
        logger.debug(f"Failed to setup known_hosts: {e}")


async def _run_git_command(args: list, cwd: str = None) -> str:
    """Run a git command and return output."""
    try:
        result = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd or str(DEFAULT_WORKSPACE)
        )
        stdout, _ = await result.communicate()
        return stdout.decode("utf-8").strip()
    except Exception as e:
        return f"Error: {e}"


@skill(
    name="git",
    description="Manage local git repositories. Commands: status, clone, commit, push, pull, branch, log, checkout, diff, add, ssh_setup"
)
async def git(command: str = "status", message: str = None, branch: str = None, 
              path: str = ".", delete: bool = False, limit: int = 10,
              repo_path: str = None) -> SkillResult:
    """Execute git commands for local repository management.
    
    Args:
        command: Git command (status, clone, commit, push, pull, branch, log, checkout, diff, add, ssh_setup)
        message: Commit message (for commit command)
        branch: Branch name (for push, branch, checkout commands)
        path: File path (for add, diff, clone commands)
        delete: Delete branch (for branch command)
        limit: Limit number of commits (for log command)
        repo_path: Repository path (optional)
    """
    cmd = command.lower()
    
    # Setup SSH key if enabled in config
    if cmd in ("clone", "push", "pull"):
        await _setup_ssh_key()
    
    if cmd == "status":
        return await _status(repo_path)
    elif cmd == "clone":
        return await _clone(path, repo_path)  # path = repo URL
    elif cmd == "ssh_setup":
        success = await _setup_ssh_key()
        if success:
            return SkillResult(success=True, output="✅ SSH key configured successfully")
        return SkillResult(success=False, error="SSH key configuration failed")
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
        return SkillResult(success=False, error=f"Unknown git command: {command}. Available: status, clone, ssh_setup, commit, push, pull, branch, log, checkout, diff, add")


async def _status(repo_path: str = None) -> SkillResult:
    """Show working tree status."""
    output = await _run_git_command(["status", "--porcelain"], repo_path)
    if not output:
        return SkillResult(success=True, output="✅ Working tree is clean")
    
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
    
    return SkillResult(success=True, output="\n".join(result))


async def _clone(repo_url: str, repo_path: str = None) -> SkillResult:
    """Clone a repository.
    
    Args:
        repo_url: Repository URL (e.g., 'https://github.com/owner/repo.git' or 'git@github.com:owner/repo.git')
        repo_path: Target directory (optional, defaults to workspace/repo-name)
    """
    if not repo_url:
        return SkillResult(success=False, error="Repository URL required for clone command")
    
    # Determine target directory
    if repo_path:
        target_dir = repo_path
    else:
        # Extract repo name from URL
        repo_name = repo_url.rstrip('/').split('/')[-1]
        if repo_name.endswith('.git'):
            repo_name = repo_name[:-4]
        target_dir = str(DEFAULT_WORKSPACE / repo_name)
    
    output = await _run_git_command(["clone", repo_url, target_dir], None)
    
    if "Error" in output:
        return SkillResult(success=False, error=output)
    
    return SkillResult(success=True, output=f"✅ Cloned repository to {target_dir}")


async def _commit(message: str, repo_path: str = None) -> SkillResult:
    """Commit staged changes."""
    if not message:
        return SkillResult(success=False, error="Commit message required")
    
    await _run_git_command(["add", "-A"], repo_path)
    status = await _run_git_command(["status", "--porcelain"], repo_path)
    
    if not status.strip():
        return SkillResult(success=True, output="Nothing to commit - working tree is clean")
    
    output = await _run_git_command(["commit", "-m", message], repo_path)
    
    if "Error" in output:
        return SkillResult(success=False, error=output)
    
    return SkillResult(success=True, output=f"✅ Committed successfully\n\n{message}")


async def _push(branch: str, repo_path: str = None) -> SkillResult:
    """Push to remote."""
    branch = branch or "main"
    output = await _run_git_command(["push", "origin", branch], repo_path)
    
    if "Error" in output:
        return SkillResult(success=False, error=output)
    if "up to date" in output.lower() or "everything up-to-date" in output.lower():
        return SkillResult(success=True, output="✅ Already up to date")
    
    return SkillResult(success=True, output=f"✅ Pushed to {branch}")


async def _pull(repo_path: str = None) -> SkillResult:
    """Pull from remote."""
    output = await _run_git_command(["pull"], repo_path)
    
    if "Error" in output:
        return SkillResult(success=False, error=output)
    if "Already up to date" in output:
        return SkillResult(success=True, output="✅ Already up to date")
    
    return SkillResult(success=True, output=f"✅ Pulled changes\n\n{output[:200]}")


async def _branch(name: str, delete: bool, repo_path: str = None) -> SkillResult:
    """List, create, or delete branches."""
    if delete and name:
        await _run_git_command(["branch", "-D", name], repo_path)
        return SkillResult(success=True, output=f"✅ Deleted branch {name}")
    
    if name:
        await _run_git_command(["checkout", "-b", name], repo_path)
        return SkillResult(success=True, output=f"✅ Created and switched to branch '{name}'")
    
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
    
    return SkillResult(success=True, output="\n".join(result))


async def _log(limit: int, repo_path: str = None) -> SkillResult:
    """Show commit history."""
    output = await _run_git_command(
        ["log", "--oneline", f"-n{limit}", "--decorate"],
        repo_path
    )
    
    if not output:
        return SkillResult(success=True, output="No commit history")
    
    lines = output.split("\n")
    result = [f"**Recent Commits** ({len(lines)}):\n"]
    for line in lines:
        if line.strip():
            result.append(f"• {line}")
    
    return SkillResult(success=True, output="\n".join(result))


async def _checkout(branch: str, repo_path: str = None) -> SkillResult:
    """Switch to a branch."""
    if not branch:
        return SkillResult(success=False, error="Branch name required")
    
    output = await _run_git_command(["checkout", branch], repo_path)
    
    if "Error" in output:
        return SkillResult(success=False, error=output)
    
    return SkillResult(success=True, output=f"✅ Switched to branch '{branch}'")


async def _diff(path: str, repo_path: str = None) -> SkillResult:
    """Show unstaged changes."""
    output = await _run_git_command(["diff", "--stat"], repo_path)
    
    if not output or output == "":
        return SkillResult(success=True, output="No unstaged changes")
    
    return SkillResult(success=True, output=f"**Unstaged Changes**\n\n{output}")


async def _add(path: str, repo_path: str = None) -> SkillResult:
    """Stage files for commit."""
    await _run_git_command(["add", path], repo_path)
    return SkillResult(success=True, output=f"✅ Staged {path}")


async def setup_ssh_key() -> bool:
    """Public wrapper for _setup_ssh_key.
    
    Called during application startup to configure SSH key for git operations.
    """
    return await _setup_ssh_key()


async def setup_git_user() -> bool:
    """Configure git user name and email from config.
    
    Reads git.user.name and git.user.email from config and sets them
    via `git config --global`.
    
    Returns:
        True if configured successfully, False if not configured.
    """
    git_config = config.get("git", {}).get("user", {})
    user_name = git_config.get("name", "").strip()
    user_email = git_config.get("email", "").strip()
    
    if not user_name or not user_email:
        return False
    
    try:
        # Set user name
        proc1 = await asyncio.create_subprocess_exec(
            "git", "config", "--global", "user.name", user_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr1 = await proc1.communicate()
        
        # Set user email
        proc2 = await asyncio.create_subprocess_exec(
            "git", "config", "--global", "user.email", user_email,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr2 = await proc2.communicate()
        
        if proc1.returncode == 0 and proc2.returncode == 0:
            logger.info(f"Git user configured: {user_name} <{user_email}>")
            return True
        else:
            if stderr1:
                logger.warning(f"Failed to set git user.name: {stderr1.decode()}")
            if stderr2:
                logger.warning(f"Failed to set git user.email: {stderr2.decode()}")
            return False
            
    except Exception as e:
        logger.warning(f"Failed to setup git user: {e}")
        return False
