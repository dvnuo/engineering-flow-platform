"""
GitHub Skill - Interact with GitHub using gh CLI.

Provides tools for cloning repositories, managing issues/PRs, and running workflows.

Supports configurable hostname for GitHub Enterprise instances.
Configuration: github.hostname in config.yaml (default: github.com)
"""

import asyncio
import shlex
from pathlib import Path
from typing import Optional

from skills.decorator import skill
from skills.executor import SkillResult

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


@skill(
    name="github",
    description="Execute any GitHub CLI (gh) command. Supports: repo clone/list, issue list/create, pr list/checkout/checks, run list/view, api, and any other gh command."
)
async def github(command: str = "repo list", args: str = None, hostname: str = None) -> SkillResult:
    """Execute GitHub CLI commands.
    
    Args:
        command: gh subcommand (repo, issue, pr, run, api, etc.)
        args: Additional arguments for the command (space-separated)
        hostname: GitHub hostname (for GitHub Enterprise)
    
    Examples:
        github(command="repo", args="clone owner/repo")
        github(command="issue", args="list --repo owner/repo --state open")
        github(command="pr", args="list --repo owner/repo")
        github(command="run", args="list --repo owner/repo")
        github(command="api", args="repos/owner/repo")
    """
    if not command:
        command = "repo list"
    
    # Build the gh command
    cmd_list = ["gh", command]
    if args:
        try:
            parsed_args = shlex.split(args)
            cmd_list.extend(parsed_args)
        except ValueError:
            cmd_list.extend(args.split())
    
    success, output = await _run_gh_command(cmd_list, hostname=hostname)
    
    if success:
        return SkillResult(success=True, output=output if output else f"✅ gh {command} completed")
    return SkillResult(success=False, error=output)
