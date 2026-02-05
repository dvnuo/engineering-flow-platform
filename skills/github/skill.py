"""
GitHub Skill - Backward compatible API.

This module re-exports from src/integrations/github/ for backward compatibility.
"""

from src.integrations.github.cli import GitHubCLI

# Global instance for backward compatibility
github_cli = GitHubCLI()

# Export for skill decorator
__all__ = ["GitHubCLI", "github_cli"]
