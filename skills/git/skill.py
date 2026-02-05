"""
Git Skill - Backward compatible API.

This module re-exports from src/integrations/git/ for backward compatibility.
"""

from src.integrations.git import GitClient, setup_ssh_key, setup_git_user

# Global instance for backward compatibility
git_client = GitClient()

# Export for skill decorator
__all__ = ["GitClient", "git_client", "setup_ssh_key", "setup_git_user"]
