"""
GitHub CLI Wrapper - gh 命令封装。

提供与 GitHub REST API 等价的功能，
但使用 gh CLI 执行（支持 Enterprise）。
"""

import asyncio
import shlex
from pathlib import Path
from typing import Optional

from config import config

DEFAULT_HOSTNAME = "github.com"


class GitHubCLI:
    """GitHub CLI wrapper using 'gh' command."""
    
    def __init__(self, hostname: str = None):
        self.hostname = hostname or config.get("github.hostname", DEFAULT_HOSTNAME)
    
    async def run(self, args: list, cwd: str = None) -> tuple:
        """Run gh command, return (success, output)."""
        cmd = ["gh"] + args
        
        if self.hostname != DEFAULT_HOSTNAME:
            cmd = ["--hostname", self.hostname] + cmd
        
        result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd or str(Path.home())
        )
        
        stdout, _ = await result.communicate()
        return result.returncode == 0, stdout.decode("utf-8").strip()
    
    async def issue_list(self, repo: str, state: str = "open") -> str:
        """List issues in repository."""
        success, output = await self.run([
            "issue", "list", 
            "--repo", repo, 
            "--state", state,
            "--limit", "10"
        ])
        return output if success else f"Error: {output}"
    
    async def pr_list(self, repo: str) -> str:
        """List PRs in repository."""
        success, output = await self.run([
            "pr", "list",
            "--repo", repo,
            "--limit", "20"
        ])
        return output if success else f"Error: {output}"


__all__ = ["GitHubCLI"]
