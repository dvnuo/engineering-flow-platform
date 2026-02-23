"""
Jira Channel - Backward compatible API.

This module re-exports from src/core/jira/ for backward compatibility.
"""

import logging
from typing import Dict

# Re-export config for backward compatibility (for tests that mock channel.jira.config)
from src.config import config

from src.jira import JiraChannel as _JiraChannel

logger = logging.getLogger(__name__)

# Valid API versions
VALID_API_VERSIONS = ("2", "3")

class JiraChannel(_JiraChannel):
    """JiraChannel that uses config from this module for backward compatibility."""
    
    def __init__(self):
        # Use config from this module (channel.jira.config) for backward compatibility
        # This allows tests to mock channel.jira.config
        # Don't call super().__init__() - we handle initialization ourselves
        jira_cfg = getattr(config, 'jira', {}) or {}
        
        # Initialize attributes directly from config.jira
        # Support both 'url' and 'base_url' for backward compatibility
        self.base_url = jira_cfg.get('url', jira_cfg.get('base_url', '')).rstrip('/')
        self.username = jira_cfg.get('username', jira_cfg.get('email', ''))
        self.password = jira_cfg.get('password', '')
        self.token = jira_cfg.get('token', '')  # Bearer token (renamed from bearer_token)
        self.project = jira_cfg.get('project', jira_cfg.get('project_key', ''))
        self.enabled = jira_cfg.get('enabled', False)
        
        # API version with validation
        api_version = jira_cfg.get('api_version', '2')
        if api_version not in VALID_API_VERSIONS:
            api_version = '2'
        self.api_version = api_version
        
        # Configurable timeout
        self.timeout = float(jira_cfg.get('timeout', 30.0))
        
        # Create HTTP client
        import httpx
        self.client = httpx.AsyncClient(timeout=self.timeout)
        
        # Initialize auth
        self._auth_header = self._get_auth_header()
        self._auth_type = self._get_auth_type()
        
        # Add backward compatibility properties
        self.email = self.username
        self.project_key = self.project
        
        # Add headers property for backward compatibility
        self.headers = self._get_headers()
        
        logger.info(f"JiraChannel initialized: version={self.api_version}, timeout={self.timeout}s, auth={self._auth_type}")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests (for backward compatibility)."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        # Add auth header
        headers.update(self._auth_header)
        return headers
    
    def is_test_case_command(self, text: str) -> bool:
        """Check if text is a test case generation command (backward compatibility)."""
        english_keywords = ['test case', 'test cases', 'generate test', 'create test']
        chinese_keywords = ['测试用例', '创建测试', '生成测试']
        
        text_lower = text.lower()
        return any(kw in text_lower for kw in english_keywords) or any(kw in text for kw in chinese_keywords)

# Global instance for backward compatibility
jira_channel = JiraChannel()

# Keep old function names for compatibility
async def jira_get_issue(issue_key: str):
    """Get a Jira issue by key."""
    return await jira_channel.get_issue(issue_key)


async def jira_search(jql: str, max_results: int = 10):
    """Search Jira issues using JQL."""
    return await jira_channel.search_issues(jql, max_results)


async def jira_add_comment(issue_key: str, comment: str):
    """Add a comment to a Jira issue."""
    return await jira_channel.add_comment(issue_key, comment)


async def jira_create_issue(
    project: str,
    summary: str,
    description: str = "",
    issue_type: str = "Task",
    priority: str = "Medium"
):
    """Create a new Jira issue."""
    return await jira_channel.create_issue(
        project=project,
        summary=summary,
        description=description,
        issue_type=issue_type,
        priority=priority
    )


async def jira_transition(issue_key: str, to_status: str, comment: str = ""):
    """Transition a Jira issue to a new status."""
    return await jira_channel.transition_issue(issue_key, to_status, comment)


async def jira_get_transitions(issue_key: str):
    """Get available status transitions for a Jira issue."""
    return await jira_channel.get_transitions(issue_key)


async def jira_get_comments(issue_key: str):
    """Get comments for a Jira issue."""
    return await jira_channel.get_comments(issue_key)


# Export classes for direct import
__all__ = [
    "JiraChannel",
    "jira_channel",
    "jira_get_issue",
    "jira_search",
    "jira_add_comment",
    "jira_create_issue",
    "jira_transition",
    "jira_get_transitions",
    "jira_get_comments",
]
