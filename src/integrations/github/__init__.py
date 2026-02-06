"""GitHub Integration - Single source of truth for GitHub operations."""

from .api import GitHubChannel as GitHubClient

__all__ = ["GitHubClient"]
