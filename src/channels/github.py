"""
GitHub Channel - Backward compatible API.

This module re-exports canonical objects from src.github.
"""

from src.github import (
    GitHubClient,
    github_channel,
    github_get_issue,
    github_search_issues,
    github_add_comment,
)

__all__ = [
    "GitHubClient",
    "github_channel",
    "github_get_issue",
    "github_search_issues",
    "github_add_comment",
]
