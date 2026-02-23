"""Git Integration - Single source of truth for Git operations."""

from .api import GitClient, setup_ssh_key, setup_git_user, setup_gh_config

__all__ = ["GitClient", "setup_ssh_key", "setup_git_user", "setup_gh_config"]


# ========== Tool Functions ==========

async def git_status(workspace: str = ".") -> str:
    """Get git status of a repository."""
    try:
        client = GitClient(workspace)
        result = await client.status()
        return result
    except Exception as e:
        return f"Error: {e}"


async def git_commit(message: str, workspace: str = ".") -> str:
    """Create a git commit."""
    try:
        client = GitClient(workspace)
        result = await client.commit(message)
        return result
    except Exception as e:
        return f"Error: {e}"


async def git_push(workspace: str = ".") -> str:
    """Push to remote."""
    try:
        client = GitClient(workspace)
        result = await client.push()
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


# ========== Git Clone Tool ==========

async def git_clone(repo_url: str, workspace: str = ".") -> str:
    """Clone a repository to the workspace."""
    try:
        client = GitClient(workspace)
        result = await client.clone(repo_url)
        return result
    except Exception as e:
        return f"Error: {e}"


def get_tools_schemas() -> list:
    """Return Git tool schemas for OpenAI."""
    # Re-read the original file content to get all schemas
    import importlib
    import sys
    # Clear cached module
    if 'src.git' in sys.modules:
        del sys.modules['src.git']
    
    from .api import GitClient
    
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
        {
            "type": "function",
            "function": {
                "name": "git_clone",
                "description": "Clone a repository from URL to workspace",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo_url": {"type": "string", "description": "Repository URL (SSH: git@github.com:owner/repo, ssh://git@github.com/owner/repo.git, or HTTPS: https://github.com/owner/repo.git)"},
                        "workspace": {"type": "string", "description": "Workspace path", "default": "."}
                    },
                    "required": ["repo_url"]
                }
            }
        },
    ]
