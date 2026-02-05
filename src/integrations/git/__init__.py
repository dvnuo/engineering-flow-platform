"""Git Integration - Single source of truth for Git operations."""

from .api import GitClient, setup_ssh_key, setup_git_user

__all__ = ["GitClient", "setup_ssh_key", "setup_git_user"]
