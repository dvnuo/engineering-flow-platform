"""Git Skill - Execute git commands."""

import asyncio
from typing import Optional

from src.git.api import GitClient
from src.agents.executor import skill


class SkillResult:
    """Result from skill execution."""
    
    def __init__(self, success: bool, output: str = "", error: Optional[str] = None):
        self.success = success
        self.output = output
        self.error = error
    
    def __str__(self) -> str:
        if self.success:
            return self.output
        return f"Error: {self.error}"


@skill(
    name="git",
    description="Execute git commands. Use for cloning repositories, checking status, committing changes, pushing, pulling, and other git operations.",
    parameters={
        "command": {
            "type": "string",
            "description": "Git subcommand: status, clone, commit, push, pull, branch, log, diff, add, checkout, fetch, merge, rebase, stash"
        },
        "args": {
            "type": "string", 
            "description": "Additional arguments (space-separated). For clone, this is the repository URL."
        },
        "cwd": {
            "type": "string",
            "description": "Working directory for the git command"
        }
    }
)
async def git(command: str = "status", args: str = "", cwd: str = None) -> str:
    """Execute a git command.
    
    Examples:
        - git(command="status")
        - git(command="clone", args="https://github.com/owner/repo.git")
        - git(command="commit", args="-m 'feat: new feature'")
        - git(command="push")
        - git(command="pull")
        - git(command="log", args="--oneline -10")
    """
    client = GitClient(cwd)
    
    # Build git command
    git_args = [command]
    if args:
        git_args.extend(args.split())
    
    # Handle clone specially - it needs a URL
    if command == "clone" and args:
        output = await client.clone(args.split()[0])
        result = SkillResult(success="Error" not in output, output=output)
        return str(result)
    
    # Run the command
    output = await client.run(git_args)
    result = SkillResult(success="Error" not in output, output=output)
    return str(result)
