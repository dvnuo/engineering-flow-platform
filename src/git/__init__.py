"""Git Integration - Single source of truth for Git operations."""

from .api import GitClient, setup_ssh_key, setup_git_user, setup_gh_config

__all__ = ["GitClient", "setup_ssh_key", "setup_git_user", "setup_gh_config"]


# ========== Tool Functions ==========

async def git_status(workspace: str = ".") -> str:
    """Get git status of a repository."""
    try:
        result = await git_client.status(workspace)
        return result
    except Exception as e:
        return f"Error: {e}"


async def git_commit(message: str, workspace: str = ".") -> str:
    """Create a git commit."""
    try:
        result = await git_client.commit(message, workspace)
        return result
    except Exception as e:
        return f"Error: {e}"


async def git_push(workspace: str = ".") -> str:
    """Push to remote."""
    try:
        result = await git_client.push(workspace)
        return result
    except Exception as e:
        return f"Error: {e}"


def get_tools_schemas() -> list:
    """Return GitHub tool schemas for OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": "git_status",
                "description": "Get git status of a repository",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {"type": "string", "description": "Workspace path", "default": "."}
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "git_commit",
                "description": "Create a git commit",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Commit message"},
                        "workspace": {"type": "string", "description": "Workspace path", "default": "."}
                    },
                    "required": ["message"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "git_push",
                "description": "Push to remote",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workspace": {"type": "string", "description": "Workspace path", "default": "."}
                    },
                    "required": []
                }
            }
        },
    ]
