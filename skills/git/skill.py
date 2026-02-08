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
async def git(command: str = "status", message: str = None, branch: str = None, limit: int = 10, repo_url: str = None, file_path: str = None, content: str = None, repo_path: str = None) -> SkillResult:
    """Execute git commands."""
    cmd = command.lower()
    
    # Determine working directory: repo_path > workspace > default
    workspace = str(Path.home() / ".efp" / "workspace")
    cwd = repo_path if repo_path else workspace
    
    if cmd == "status":
        output = await git_client.run(["status"], cwd)
        return SkillResult(success=True, output=output)
    elif cmd == "log":
        output = await git_client.run(["log", f"-n{limit}", "--pretty=format:%h %s"], cwd)
        return SkillResult(success=True, output=output)
    elif cmd == "branch":
        output = await git_client.run(["branch", "-a"], cwd)
        return SkillResult(success=True, output=output)
    elif cmd == "commit" and message:
        output = await git_client.run(["commit", "-m", message], cwd)
        return SkillResult(success=True, output=output)
    elif cmd == "push":
        output = await git_client.run(["push"], cwd)
        return SkillResult(success=True, output=output)
    elif cmd == "pull":
        output = await git_client.run(["pull"], cwd)
        return SkillResult(success=True, output=output)
    elif cmd == "clone" and repo_url:
        output = await git_client.clone(repo_url)
        return SkillResult(success=True, output=output)
    elif cmd == "add" and file_path:
        output = await git_client.run(["add", file_path], cwd)
        return SkillResult(success=True, output=output)
    
    return SkillResult(success=False, error=f"Unknown command: {command}")


# Export for backward compatibility
__all__ = ["git", "git_client", "GitClient", "setup_ssh_key", "setup_git_user"]
