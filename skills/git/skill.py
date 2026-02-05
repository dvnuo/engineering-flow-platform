"""
Git Skill - Local Git Management for AI.

Execute any git command with flexible arguments.

Usage:
- git status → Check working tree status
- git commit -m "message" → Commit changes
- git push → Push to remote
- git pull → Pull from remote
- git branch → List branches
- git checkout <branch> → Switch branches
- git rebase <branch> → Rebase onto branch
- git stash → Stash changes
- etc.

Supports any git command with arguments.
"""

import asyncio
import shlex
import logging
import os
import shutil
import yaml
from pathlib import Path
from typing import Optional

from skills.executor import SkillResult, skill
from config import config

logger = logging.getLogger(__name__)

# Default workspace path using home directory
DEFAULT_WORKSPACE = Path.home() / ".opsclaw" / "workspace"

# Flag to track if SSH key has been setup
_ssh_key_setup_done = False


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
    description="Execute any git command. Supports: status, clone, commit, push, pull, branch, checkout, log, diff, add, rebase, stash, merge, reset, fetch, tag, remote, blame, grep, show, bisect, and any other git command. Use 'command' for the git subcommand and 'args' for additional arguments."
)
async def git(command: str = "status", args: str = None, cwd: str = None) -> SkillResult:
    """Execute git commands for local repository management.
    
    Args:
        command: Git subcommand (status, clone, commit, push, pull, branch, checkout, log, diff, add, rebase, stash, merge, reset, fetch, tag, remote, blame, grep, show, bisect, etc.)
        args: Additional arguments for the git command (space-separated)
        cwd: Working directory (defaults to ~/.opsclaw/workspace)
    
    Examples:
        git(command="status")
        git(command="clone", args="https://github.com/owner/repo.git")
        git(command="commit", args="-m 'fix: update'")
        git(command="checkout", args="develop")
        git(command="rebase", args="main")
        git(command="stash", args="push -m 'WIP'")
        git(command="cherry-pick", args="abc123")
        git(command="reset", args="--hard HEAD~1")
        git(command="merge", args="feature-branch")
    
    Common workflows:
        # Check status and stage all changes
        git(command="add", args="-A")
        
        # Create and switch to new branch
        git(command="checkout", args="-b feature/new-feature")
        
        # View recent commits
        git(command="log", args="--oneline -10")
        
        # Fetch and prune
        git(command="fetch", args="--all --prune")
    """
    if not command:
        command = "status"
    
    # Build the git command
    cmd_list = ["git", command]
    if args:
        try:
            parsed_args = shlex.split(args)
            cmd_list.extend(parsed_args)
        except ValueError:
            cmd_list.extend(args.split())
    
    # Run the command
    output = await _run_git_command(cmd_list, cwd)
    
    # Check for errors
    if "Error" in output or "fatal:" in output or "failed" in output.lower():
        return SkillResult(success=False, error=output)
    
    return SkillResult(success=True, output=output if output else f"✅ git {command} completed")


async def _setup_ssh_key() -> bool:
    """Setup SSH key from config."""
    global _ssh_key_setup_done
    
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
    
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    
    dest_path = ssh_dir / source_path.name
    try:
        shutil.copy2(source_path, dest_path)
        os.chmod(dest_path, 0o600)
        _ssh_key_setup_done = True
        logger.info(f"SSH key configured: {dest_path}")
        return True
    except Exception as e:
        logger.debug(f"Failed to setup SSH key: {e}")
        return False


async def setup_ssh_key() -> bool:
    """Public wrapper for SSH key setup."""
    return await _setup_ssh_key()


async def setup_git_user() -> bool:
    """Configure git user from config."""
    git_config = config.get("git", {}).get("user", {})
    user_name = git_config.get("name", "").strip()
    user_email = git_config.get("email", "").strip()
    
    if not user_name or not user_email:
        return False
    
    try:
        await _run_git_command(["config", "--global", "user.name", user_name])
        await _run_git_command(["config", "--global", "user.email", user_email])
        logger.info(f"Git user configured: {user_name} <{user_email}>")
        return True
    except Exception as e:
        logger.debug(f"Failed to setup git user: {e}")
        return False


async def _resolve_env_var(value: str) -> str:
    """Resolve environment variable in config value (e.g., "${VAR_NAME}" -> value)."""
    if value.startswith("${") and value.endswith("}"):
        var_name = value[2:-1]
        return os.environ.get(var_name, "")
    return value


async def setup_gh_config() -> bool:
    """Configure GitHub CLI (gh) from github config.
    
    Sets up ~/.config/gh/hosts.yml for GitHub authentication.
    Uses base_url to configure enterprise GitHub instances.
    
    Returns:
        True if gh was configured successfully
    """
    github_config = config.get("github", {})
    if not github_config.get("enabled", False):
        logger.debug("GitHub config not enabled (github.enabled=false)")
        return False
    
    # Get token
    token = await _resolve_env_var(github_config.get("api_token", ""))
    if not token:
        logger.debug("No GitHub token configured")
        return False
    
    # Get base_url (hostname for gh config)
    base_url = await _resolve_env_var(github_config.get("base_url", ""))
    user = await _resolve_env_var(github_config.get("user", ""))
    
    # Build hosts configuration
    hosts_config = {}
    
    if base_url:
        # Enterprise GitHub - use base_url as hostname
        # Remove https:// prefix if present
        gh_hostname = base_url.replace("https://", "").replace("http://", "")
        hosts_config[gh_hostname] = {
            "users": {
                "user": {
                    "oauth_token": token,
                }
            },
            "git_protocol": "ssh",
            "oauth_token": token,
            "user": user,
        }
        logger.info(f"GitHub enterprise config: {gh_hostname}")
    else:
        # Default to github.com
        hosts_config["github.com"] = {
            "oauth_token": token,
            "user": user,
        }
        logger.info("GitHub cloud (github.com) token configured")
    
    # Create gh config directory
    gh_config_dir = Path.home() / ".config" / "gh"
    gh_config_dir.mkdir(parents=True, exist_ok=True)
    
    # Write hosts.yml
    hosts_file = gh_config_dir / "hosts.yml"
    try:
        # Write YAML with proper formatting
        with open(hosts_file, "w") as f:
            f.write("# GitHub CLI configuration\n")
            f.write("# Generated by OpsClaw\n\n")
            yaml.dump(hosts_config, f, default_flow_style=False, allow_unicode=True)
        
        # Set proper permissions (gh requires strict permissions)
        os.chmod(hosts_file, 0o600)
        logger.info(f"GH config written: {hosts_file}")
        return True
    except Exception as e:
        logger.debug(f"Failed to write gh config: {e}")
        return False
