"""
Git Skill - Backward compatible API.

This module re-exports from src/integrations/git/ for backward compatibility.
"""

from skills.executor import SkillResult, skill
from src.integrations.git import GitClient, setup_ssh_key, setup_git_user

# Global instance for backward compatibility
git_client = GitClient()


@skill(
    name="git",
    description="Manage local git repositories. Commands: status, log, branch, commit, push, pull"
)
async def git(command: str = "status", message: str = None, branch: str = None, limit: int = 10) -> SkillResult:
    """Execute git commands."""
    cmd = command.lower()
    
    if cmd == "status":
        output = await git_client.run(["status"])
        return SkillResult(success=True, output=output)
    elif cmd == "log":
        output = await git_client.run(["log", f"-n{limit}", "--pretty=format:%h %s"])
        return SkillResult(success=True, output=output)
    elif cmd == "branch":
        output = await git_client.run(["branch", "-a"])
        return SkillResult(success=True, output=output)
    elif cmd == "commit" and message:
        success, output = await git_client.run(["commit", "-m", message])
        return SkillResult(success=success, output=output)
    elif cmd == "push":
        success, output = await git_client.run(["push"])
        return SkillResult(success=success, output=output)
    elif cmd == "pull":
        success, output = await git_client.run(["pull"])
        return SkillResult(success=success, output=output)
    
    return SkillResult(success=False, error=f"Unknown command: {command}")


# Export for backward compatibility
__all__ = ["git", "git_client", "GitClient", "setup_ssh_key", "setup_git_user"]
