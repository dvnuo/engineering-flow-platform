"""
Git Integration - Single source of truth for Git operations.
"""

import asyncio
import shlex
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML

from src.config import config

logger = logging.getLogger(__name__)

# Default workspace path using home directory
DEFAULT_WORKSPACE = Path.home() / ".efp" / "workspace"

# Module-level YAML instance
_yaml = YAML()


class GitClient:
    """Git client for repository operations."""
    
    def __init__(self, workspace: str = None):
        self.workspace = workspace or str(DEFAULT_WORKSPACE)
    
    async def run(self, args: list, cwd: str = None, env: dict = None) -> str:
        """Run a git command and return output.
        
        Args:
            args: Git command arguments
            cwd: Working directory
            env: Environment variables to add/modify
        """
        try:
            # Setup environment
            process_env = None
            if env:
                process_env = os.environ.copy()
                process_env.update(env)
            
            result = await asyncio.create_subprocess_exec(
                "git", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd or self.workspace,
                env=process_env
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
    
    def convert_to_ssh(self, https_url: str) -> str:
        """Convert HTTPS git URL to SSH URL.
        
        Supports formats:
        - https://github.com/owner/repo.git -> git@github.com:owner/repo
        - https://github.com/owner/repo -> git@github.com:owner/repo
        """
        import re
        # Match HTTPS URL pattern: https://hostname/path
        match = re.match(r'https://([^/]+)/(.+)', https_url)
        if match:
            hostname = match.group(1)
            path = match.group(2)
            # Remove .git suffix if present
            if path.endswith('.git'):
                path = path[:-4]
            return f"git@{hostname}:{path}"
        return https_url  # Return as-is if not HTTPS format
    
    def convert_to_https(self, ssh_url: str) -> str:
        """Convert SSH git URL to HTTPS URL.
        
        Supports formats:
        - git@github.com:owner/repo.git -> https://github.com/owner/repo.git
        - git@github.com:owner/repo -> https://github.com/owner/repo
        - ssh://git@github.com/owner/repo.git -> https://github.com/owner/repo.git
        """
        # Match SSH URL pattern: git@hostname:path or ssh://git@hostname/path
        # Pattern handles both formats with or without .git suffix
        match = re.match(r'(?:git@|ssh://git@)([^/:]+):?/?(.+)$', ssh_url)
        if match:
            hostname = match.group(1)
            path = match.group(2)
            # Remove .git suffix if present for clean URL
            if path.endswith('.git'):
                path = path[:-4]
            return f"https://{hostname}/{path}"
        return ssh_url  # Return as-is if not SSH format
    
    async def clone(self, repo_url: str, target_dir: str = None) -> str:
        """Clone a repository.
        
        Supports SSH and HTTPS URLs:
        - SSH: git@github.com:owner/repo.git
        - HTTPS: https://github.com/owner/repo.git
        
        When using HTTPS URLs, automatically converts to SSH format
        and configures SSH to skip host key verification for automation.
        
        Note: Using StrictHostKeyChecking=no for automation convenience.
        For production, consider using StrictHostKeyChecking=accept-new
        to detect MITM attacks on known hosts.
        """
        # Convert HTTPS URL to SSH if needed
        if repo_url.startswith("https://"):
            repo_url = self.convert_to_ssh(repo_url)
        
        # Extract repo name from URL if no target_dir specified
        if not target_dir:
            repo_name = repo_url.split("/")[-1].replace(".git", "")
            target_dir = os.path.join(self.workspace, repo_name)
        
        # Ensure target directory
        target = os.path.join(self.workspace, target_dir.split("/")[-1].replace(".git", ""))
        os.makedirs(self.workspace, exist_ok=True)
        
        # Configure git to skip host key verification for automation
        # WARNING: This reduces security by not verifying host keys.
        # Consider using 'accept-new' instead of 'no' for better security.
        ssh_env = {
            'GIT_SSH_COMMAND': (
                'ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null'
            )
        }
        
        try:
            output = await self.run(["clone", repo_url, target], cwd=self.workspace, env=ssh_env)
            
            # Check for common errors and provide helpful messages
            if "Error:" in output:
                if "Could not resolve host" in output:
                    return f"Error: Could not resolve host. Please check the repository URL: {repo_url}"
                elif "Repository not found" in output:
                    return f"Error: Repository not found. Please check the URL: {repo_url}"
                elif "Permission denied" in output:
                    return f"Error: Permission denied. You may need to add your SSH key or check permissions."
                elif "Authentication failed" in output:
                    return f"Error: Authentication failed. Please check your SSH key configuration."
            
            return output if output else f"Successfully cloned to {target}"
            
        except Exception as e:
            return f"Error: {e}"


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


async def generate_ssh_key(key_type: str = "rsa", comment: str = "engineering-flow-platform") -> dict:
    """Generate SSH key pair.
    
    Args:
        key_type: Key type (ed25519 or rsa)
        comment: Comment for the key
        
    Returns:
        dict with success status, public key, and key path
    """
    import asyncio
    import subprocess
    
    def _do_generate():
        ssh_dir = Path.home() / ".ssh"
        ssh_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(ssh_dir, 0o700)
        
        # Determine key file path
        if key_type == "ed25519":
            private_key_path = ssh_dir / "id_ed25519"
            public_key_path = ssh_dir / "id_ed25519.pub"
        else:
            private_key_path = ssh_dir / "id_rsa"
            public_key_path = ssh_dir / "id_rsa.pub"
        
        # Check if private key exists
        if private_key_path.exists():
            # Try to read existing public key
            if public_key_path.exists():
                with open(public_key_path, 'r') as f:
                    public_key = f.read().strip()
                return {
                    "success": True,
                    "message": "SSH key already exists",
                    "public_key": public_key,
                    "key_type": key_type
                }
            else:
                # Private key exists but public key missing - derive public key
                try:
                    result = subprocess.run(
                        ["ssh-keygen", "-y", "-f", str(private_key_path)],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0:
                        public_key = result.stdout.strip()
                        # Save the derived public key
                        with open(public_key_path, 'w') as f:
                            f.write(public_key + "\n")
                        os.chmod(public_key_path, 0o644)
                        return {
                            "success": True,
                            "message": "Public key derived from existing private key",
                            "public_key": public_key,
                            "key_type": key_type
                        }
                except Exception as e:
                    logger.warning(f"Could not derive public key: {e}")
        
        # Generate new key
        cmd = [
            "ssh-keygen",
            "-t", key_type,
            "-f", str(private_key_path),
            "-N", "",  # No passphrase
            "-C", comment
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            logger.error(f"SSH key generation failed: {result.stderr}")
            return {
                "success": False,
                "error": result.stderr
            }
        
        # Set proper permissions
        os.chmod(private_key_path, 0o600)
        os.chmod(public_key_path, 0o644)
        
        # Read and return public key
        with open(public_key_path, 'r') as f:
            public_key = f.read().strip()
        
        logger.info(f"SSH {key_type} key generated at {private_key_path}")
        
        return {
            "success": True,
            "message": f"SSH {key_type} key generated successfully",
            "public_key": public_key,
            "key_type": key_type
        }
    
    # Run in thread pool to avoid blocking event loop
    try:
        result = await asyncio.to_thread(_do_generate)
        return result
    except Exception as e:
        logger.error(f"SSH key generation error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


async def get_ssh_public_key() -> dict:
    """Get existing SSH public key.
    
    Returns:
        dict with public key if exists
    """
    ssh_dir = Path.home() / ".ssh"
    
    # Check for ed25519 first, then rsa
    for key_type in ["ed25519", "rsa"]:
        if key_type == "ed25519":
            public_key_path = ssh_dir / "id_ed25519.pub"
        else:
            public_key_path = ssh_dir / "id_rsa.pub"
        
        if public_key_path.exists():
            with open(public_key_path, 'r') as f:
                public_key = f.read().strip()
            return {
                "success": True,
                "public_key": public_key,
                "key_type": key_type
            }
    
    return {
        "success": False,
        "message": "No SSH key found. Generate one first."
    }


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
    user = github_config.get("user", "")
    if not token:
        return False
    
    base_url = github_config.get("base_url", "")
    gh_config_dir = Path.home() / ".config" / "gh"
    gh_config_dir.mkdir(parents=True, exist_ok=True)
    
    hosts_file = gh_config_dir / "hosts.yml"
    hostname = base_url.replace("https://", "").replace("http://", "") if base_url else "github.com"
    
    hosts_config = {hostname: {"oauth_token": token, "user": user}}
    if not base_url:
        hosts_config = {"github.com": {"oauth_token": token, "user": ""}}
    
    with open(hosts_file, "w") as f:
        _yaml.dump(hosts_config, f)
    
    os.chmod(hosts_file, 0o600)
    return True


__all__ = ["GitClient", "setup_ssh_key", "setup_git_user", "setup_gh_config"]
