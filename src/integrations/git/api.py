"""
Git Integration - Single source of truth for Git operations.
"""

import asyncio
import shlex
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

# Default workspace path using home directory
DEFAULT_WORKSPACE = Path.home() / ".engineering-flow-platform" / "workspace"


class GitClient:
    """Git client for repository operations."""
    
    def __init__(self, workspace: str = None):
        self.workspace = workspace or str(DEFAULT_WORKSPACE)
    
    async def run(self, args: list, cwd: str = None) -> str:
        """Run a git command and return output."""
        try:
            result = await asyncio.create_subprocess_exec(
                "git", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd or self.workspace
            )
            stdout, _ = await result.communicate()
            return stdout.decode("utf-8").strip()
        except Exception as e:
            return f"Error: {e}"
    
    async def status(self, cwd: str = None) -> str:
        """Get git status."""
        return await self.run(["status"], cwd)
    
    async def commit(self, message: str, cwd: str = None) -> str:
        """Create a commit with message."""
        return await self.run(["commit", "-m", message], cwd)
    
    async def push(self, cwd: str = None) -> str:
        """Push to remote."""
        return await self.run(["push"], cwd)
    
    async def pull(self, cwd: str = None) -> str:
        """Pull from remote."""
        return await self.run(["pull"], cwd)


# Standalone functions for backward compatibility
async def _run_git_command(args: list, cwd: str = None) -> str:
    """Run a git command and return output."""
    client = GitClient(cwd)
    return await client.run(args)


async def setup_ssh_key() -> bool:
    """Setup SSH key from config."""
    ssh_config = config.get("ssh", {})
    if not ssh_config.get("enabled", False):
        return False
    
    private_key_path = ssh_config.get("private_key_path", "")
    if not private_key_path or not os.path.exists(private_key_path):
        return False
    
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dir, 0o700)
    
    shutil.copy(private_key_path, ssh_dir / "id_rsa")
    os.chmod(ssh_dir / "id_rsa", 0o600)
    
    logger.info(f"SSH key configured from {private_key_path}")
    return True


async def setup_git_user() -> bool:
    """Setup git user from config."""
    git_config = config.get("git", {})
    user_name = git_config.get("user", {}).get("name", "")
    user_email = git_config.get("user", {}).get("email", "")
    
    if not user_name or not user_email:
        return False
    
    await _run_git_command(["config", "--global", "user.name", user_name])
    await _run_git_command(["config", "--global", "user.email", user_email])
    
    logger.info(f"Git user configured: {user_name} <{user_email}>")
    return True


async def setup_gh_config() -> bool:
    """Configure GitHub CLI (gh) from github config."""
    github_config = config.get("github", {})
    if not github_config.get("enabled", False):
        return False
    
    token = github_config.get("api_token", "")
    if not token:
        return False
    
    base_url = github_config.get("base_url", "")
    gh_config_dir = Path.home() / ".config" / "gh"
    gh_config_dir.mkdir(parents=True, exist_ok=True)
    
    hosts_file = gh_config_dir / "hosts.yml"
    hostname = base_url.replace("https://", "").replace("http://", "") if base_url else "github.com"
    
    import yaml
    hosts_config = {hostname: {"oauth_token": token, "user": ""}}
    if not base_url:
        hosts_config = {"github.com": {"oauth_token": token, "user": ""}}
    
    with open(hosts_file, "w") as f:
        yaml.dump({"hosts": hosts_config}, f)
    
    os.chmod(hosts_file, 0o600)
    return True


__all__ = ["GitClient", "setup_ssh_key", "setup_git_user", "setup_gh_config"]
