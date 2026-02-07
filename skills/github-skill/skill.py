"""
GitHub Skill - Backward compatible API.

This module re-exports from src/integrations/github/ for backward compatibility.
"""

from skills.executor import SkillResult, skill
from src.integrations.github.cli import GitHubCLI

# Global instance for backward compatibility
github_cli = GitHubCLI()


@skill(
    name="github",
    description="Interact with GitHub using gh CLI. Commands: status, issue list, pr list, repo clone"
)
async def github(command: str = "status", repo: str = None) -> SkillResult:
    """Execute GitHub CLI commands."""
    cmd = command.lower()
    
    if cmd == "status":
        success, output = await github_cli.run(["status"])
        return SkillResult(success=success, output=output or "GitHub CLI ready")
    elif cmd == "issue_list" and repo:
        output = await github_cli.issue_list(repo)
        return SkillResult(success=True, output=output)
    elif cmd == "pr_list" and repo:
        output = await github_cli.pr_list(repo)
        return SkillResult(success=True, output=output)
    elif cmd == "repo_clone" and repo:
        success, output = await github_cli.run(["repo", "clone", repo])
        return SkillResult(success=success, output=output)
    
    return SkillResult(success=False, error=f"Unknown command: {command}")


# Export for backward compatibility
__all__ = ["github", "github_cli", "GitHubCLI"]
