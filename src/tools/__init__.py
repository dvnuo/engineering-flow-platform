"""
Tools - Unified tool exports for Engineering Flow Platform.

This module exports all tools from src/tools/* for agent use.
"""

from .github import get_tools_schemas as get_github_tools
from .jira import get_tools_schemas as get_jira_tools
from .confluence import get_tools_schemas as get_confluence_tools
from .git import get_tools_schemas as get_git_tools

# Also export raw functions for backward compatibility
from . import github
from . import jira
from . import confluence
from . import git


def get_all_tools() -> list:
    """Get all tool schemas."""
    tools = []
    tools.extend(get_github_tools())
    tools.extend(get_jira_tools())
    tools.extend(get_confluence_tools())
    tools.extend(get_git_tools())
    return tools


__all__ = [
    "get_all_tools",
    "get_github_tools",
    "get_jira_tools",
    "get_confluence_tools",
    "get_git_tools",
    "github",
    "jira",
    "confluence",
    "git",
]
