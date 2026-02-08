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
    description="Manage local git repositories. Commands: status, log, branch, commit, push, pull, clone, add"
)
async def git(command: str = "status", message: str = None, branch: str = None, limit: int = 10, repo_url: str = None, file_path: str = None, content: str = None) -> SkillResult:
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
        output = await git_client.run(["commit", "-m", message])
        return SkillResult(success=True, output=output)
    elif cmd == "push":
        output = await git_client.run(["push"])
        return SkillResult(success=True, output=output)
    elif cmd == "pull":
        output = await git_client.run(["pull"])
        return SkillResult(success=True, output=output)
    elif cmd == "clone" and repo_url:
        output = await git_client.clone(repo_url)
        return SkillResult(success=True, output=output)
    elif cmd == "add" and file_path:
        output = await git_client.run(["add", file_path])
        return SkillResult(success=True, output=output)
    
    return SkillResult(success=False, error=f"Unknown command: {command}")


# Export for backward compatibility
__all__ = ["git", "git_client", "GitClient", "setup_ssh_key", "setup_git_user"]
