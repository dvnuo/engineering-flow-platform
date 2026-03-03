"""
Jira Format Adapter - Unified interface for Markdown/wiki/raw formats.

Provides:
- Query: get_issue (includes comments; default: Markdown)
- Write: create_issue, update_issue, add_comment (default: Markdown)
"""

import logging
from typing import Any, Dict, List, Optional, Union

from ..utils.truncate import truncate
from .api import JiraChannel
from .converter import converter

logger = logging.getLogger(__name__)


class JiraFormatAdapter:
    """Unified interface for Jira operations with format conversion."""
    
    def __init__(self, channel: JiraChannel):
        self.channel = channel
        self.converter = converter
        # 部署类型：从 api_version 判断
        # v2 = Server/DC (wiki), v3 = Cloud (ADF)
        self.deployment = 'cloud' if getattr(channel, 'api_version', '2') == '3' else 'server'
    
    # ========== Query Operations ==========
    
    async def get_issue(
        self,
        issue_key: str,
        format: str = "markdown",
        max_chars: int = None,
        max_comments: int = 5,
        include_fields: List[str] = None,
        include_comments: bool = True
    ) -> Union[str, dict]:
        """Get Jira issue.
        
        Args:
            issue_key: Jira issue key (e.g., 'PROJ-123')
            format: Output format - "markdown" (default), "wiki", or "raw"
            max_chars: Maximum characters to return
            max_comments: Maximum number of comments to include
            include_fields: Fields to include (default: summary, status, description, comments)
            include_comments: Whether to include comments
            
        Returns:
            markdown/wiki: str
            raw: dict (full issue JSON)
        """
        issue = await self.channel.get_issue(issue_key)
        
        if format == "raw":
            return self._format_raw(issue, include_fields, include_comments, max_comments)
        
        if format == "wiki":
            return self._to_wiki(issue, max_chars, max_comments, include_fields, include_comments)
        
        # format == "markdown" (default)
        return self._to_markdown(issue, max_chars, max_comments, include_fields, include_comments)
    
    def _to_markdown(
        self,
        issue: dict,
        max_chars: int = None,
        max_comments: int = 5,
        include_fields: List[str] = None,
        include_comments: bool = True
    ) -> str:
        """Convert issue to Markdown format."""
        fields = include_fields or ["summary", "status", "description", "comments"]
        lines = []
        
        # Get issue key
        issue_key = issue.get("key", "")
        
        # Summary with key
        if "summary" in fields:
            summary = issue.get("fields", {}).get("summary", "")
            if issue_key:
                lines.append(f"# {issue_key}: {summary}")
            else:
                lines.append(f"# {summary}")
        
        # Status, Type, Priority
        if "status" in fields:
            status = issue.get("fields", {}).get("status", {})
            lines.append(f"**Status:** {status.get('name', 'Unknown')}")
        
        if "issuetype" in fields:
            issue_type = issue.get("fields", {}).get("issuetype", {})
            lines.append(f"**Type:** {issue_type.get('name', 'Task')}")
        
        if "priority" in fields:
            priority = issue.get("fields", {}).get("priority")
            if priority:
                lines.append(f"**Priority:** {priority.get('name', 'None')}")
        
        if "assignee" in fields:
            assignee = issue.get("fields", {}).get("assignee")
            if assignee:
                lines.append(f"**Assignee:** {assignee.get('displayName', 'Unassigned')}")
        
        # Description
        if "description" in fields:
            desc = issue.get("fields", {}).get("description")
            if desc:
                desc_md = self._convert_description_to_markdown(desc)
                if desc_md:
                    lines.append(f"\n## Description\n{desc_md}")
        
        # Comments
        if include_comments and "comments" in fields:
            comments = self._get_comments_list(issue, max_comments)
            if comments:
                lines.append(f"\n## Comments ({len(comments)})")
                for i, comment in enumerate(comments, 1):
                    lines.append(f"\n### Comment {i}")
                    lines.append(f"**{comment.get('author', {}).get('displayName', 'Unknown')}** - {comment.get('created', '')[:10]}")
                    body = comment.get("body", {})
                    body_md = self._convert_description_to_markdown(body)
                    lines.append(body_md)
        
        result = "\n".join(lines)
        
        # Apply character limit
        if max_chars:
            result = truncate(result, max_chars)
        
        return result
    
    def _to_wiki(
        self,
        issue: dict,
        max_chars: int = None,
        max_comments: int = 5,
        include_fields: List[str] = None,
        include_comments: bool = True
    ) -> str:
        """Convert issue to Jira wiki format."""
        fields = include_fields or ["summary", "status", "description", "comments"]
        lines = []
        
        # Get issue key
        issue_key = issue.get("key", "")
        
        # Summary with key
        if "summary" in fields:
            summary = issue.get("fields", {}).get("summary", "")
            if issue_key:
                lines.append(f"h1. {issue_key}: {summary}")
            else:
                lines.append(f"h1. {summary}")
        
        # Status
        if "status" in fields:
            status = issue.get("fields", {}).get("status", {})
            lines.append(f"*Status:* {status.get('name', 'Unknown')}")
        
        # Description
        if "description" in fields:
            desc = issue.get("fields", {}).get("description")
            if desc:
                desc_wiki = self._convert_description_to_wiki(desc)
                if desc_wiki:
                    lines.append(f"\n{desc_wiki}")
        
        # Comments (only if explicitly requested)
        if include_comments and "comments" in fields:
            comments = self._get_comments_list(issue, max_comments)
            if comments:
                lines.append("\n-- Comments --")
                for i, comment in enumerate(comments, 1):
                    author = comment.get("author", {}).get("displayName", "Unknown")
                    created = comment.get("created", "")[:10]
                    body = comment.get("body", {})
                    body_wiki = self._convert_description_to_wiki(body)
                    lines.append(f"\n*Comment {i}* - {author} ({created})")
                    lines.append(body_wiki)
        
        result = "\n".join(lines)
        
        # Apply character limit
        if max_chars:
            result = truncate(result, max_chars)
        
        return result
    
    def _format_raw(
        self,
        issue: dict,
        include_fields: List[str] = None,
        include_comments: bool = True,
        max_comments: int = 5
    ) -> dict:
        """Return raw issue dict with filtered fields."""
        if include_fields:
            filtered = {"key": issue.get("key")}
            fields = issue.get("fields", {})
            
            for field in include_fields:
                if field in fields:
                    filtered[field] = fields[field]
            
            if include_comments and "comments" in include_fields:
                filtered["comments"] = self._get_comments_list(issue, max_comments)
            
            return filtered
        
        return issue
    
    def _convert_description_to_markdown(self, desc: Any) -> str:
        """Convert description to Markdown."""
        if not desc:
            return ""
        
        # Check if ADF (Cloud)
        if isinstance(desc, dict) and desc.get("type") == "doc":
            return self.converter.adf_to_markdown(desc)
        
        # Wiki/string (Server/DC)
        if isinstance(desc, str):
            return self.converter.wiki_to_markdown(desc)
        
        return str(desc)
    
    def _convert_description_to_wiki(self, desc: Any) -> str:
        """Convert description to wiki."""
        if not desc:
            return ""
        
        # ADF - convert to wiki first
        if isinstance(desc, dict) and desc.get("type") == "doc":
            md = self.converter.adf_to_markdown(desc)
            return self.converter.markdown_to_wiki(md)
        
        # Already wiki
        if isinstance(desc, str):
            return desc
        
        return str(desc)
    
    def _get_comments_list(self, issue: dict, max_comments: int = 5) -> List[dict]:
        """Extract comments from issue."""
        fields = issue.get("fields", {})
        comments = fields.get("comment", {})
        
        if isinstance(comments, dict):
            return comments.get("comments", [])[:max_comments]
        elif isinstance(comments, list):
            return comments[:max_comments]
        
        return []
    
    # ========== Write Operations ==========
    
    async def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str = "",
        description_format: str = "markdown",
        issue_type: str = "Bug",
        priority: str = None,
        assignee: str = None,
        labels: List[str] = None
    ) -> str:
        """Create a new Jira issue.
        
        Args:
            project_key: Project key (e.g., 'PROJ')
            summary: Issue summary/title
            description: Issue description
            description_format: Input format - "markdown" (default), "wiki", or "raw"
            issue_type: Issue type (Task, Bug, Story, etc.)
            priority: Priority name
            assignee: Assignee account ID or email
            labels: List of labels
            
        Returns:
            Success message with issue key and URL
        """
        # Convert description if needed
        converted_desc = self._convert_description(description, description_format)
        
        result = await self.channel.create_issue(
            project=project_key,
            summary=summary,
            description=converted_desc,
            issue_type=issue_type,
            priority=priority,
            assignee=assignee,
            labels=labels
        )
        
        if isinstance(result, dict):
            issue_key = result.get("key", "")
            url = result.get("self", "")
            # Extract base URL
            if url:
                url = url.split("/rest/")[0] + f"/browse/{issue_key}"
            return f"Issue created: [{issue_key}]({url})"
        
        return str(result)
    
    async def add_comment(
        self,
        issue_key: str,
        body: str,
        body_format: str = "markdown"
    ) -> str:
        """Add a comment to a Jira issue.
        
        Args:
            issue_key: Jira issue key
            body: Comment body
            body_format: Input format - "markdown" (default), "wiki", or "raw"
            
        Returns:
            Success message
        """
        # Convert body if needed
        converted_body = self._convert_description(body, body_format)
        
        result = await self.channel.add_comment(issue_key, converted_body)
        
        if result:
            return f"Comment added to {issue_key}"
        
        return f"Error adding comment to {issue_key}"
    
    async def update_issue(
        self,
        issue_key: str,
        summary: str = None,
        description: str = None,
        description_format: str = "markdown",
        priority: str = None,
        labels: List[str] = None
    ) -> str:
        """Update a Jira issue.
        
        Args:
            issue_key: Jira issue key
            summary: New summary (optional)
            description: New description (optional)
            description_format: Input format - "markdown" (default), "wiki", or "raw"
            priority: New priority (optional)
            labels: New labels (optional)
            
        Returns:
            Success message
        """
        # Convert description if provided
        converted_desc = None
        if description is not None:
            converted_desc = self._convert_description(description, description_format)
        
        result = await self.channel.update_issue(
            issue_key=issue_key,
            summary=summary,
            description=converted_desc,
            priority=priority,
            labels=labels
        )
        
        if result:
            return f"Issue {issue_key} updated successfully"
        
        return f"Error updating issue {issue_key}"
    
    def _convert_description(self, text: str, format: str) -> str:
        """Convert description/comment body to appropriate format.
        
        Args:
            text: Input text
            format: Input format - "markdown", "wiki", or "raw"
            
        Returns:
            - For wiki (Server/DC): wiki markup string
            - For Cloud: ADF dict (not JSON string)
        """
        if format == "markdown":
            if self.deployment == "cloud":
                # Cloud: return ADF dict directly (channel methods should handle dict)
                return self.converter.markdown_to_adf(text)
            else:
                # Server/DC: convert to wiki
                return self.converter.markdown_to_wiki(text)
        
        # format == "wiki" or "raw" - return as-is
        return text
