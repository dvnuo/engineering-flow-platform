"""
Jira Channel - Full API support for Jira issue management.

Features:
- Get, create, update, transition issues
- JQL search with validation
- Comment management
- Webhook handling for inbound events
"""

import base64
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)

# Security: JQL injection patterns to block
JQL_DANGEROUS_PATTERNS = [
    r";",           # Statement separator
    r"--",          # SQL comment
    r"/\*",         # Block comment start
    r"\*/",         # Block comment end
    r"xp_",         # Extended stored procedures
    r"exec\s",      # EXEC command
    r"execute\s",   # EXECUTE command
    r"delete\s",    # DELETE statement
    r"drop\s",      # DROP statement
    r"truncate\s",  # TRUNCATE statement
]


class JiraChannel:
    """Jira channel adapter with full REST API support."""
    
    def __init__(self):
        self.base_url = config.jira.get("url", "").rstrip("/")
        self.username = config.jira.get("username", "")
        self.api_token = config.jira.get("api_token", "")
        self.project = config.jira.get("project", "")
        self.enabled = config.jira.get("enabled", False)
        
        self.client = httpx.AsyncClient(timeout=30.0)
        self._auth_header = self._get_auth_header()
    
    def _get_auth_header(self) -> Dict[str, str]:
        """Get authorization header."""
        if self.username and self.api_token:
            creds = f"{self.username}:{self.api_token}"
            token = base64.b64encode(creds.encode()).decode()
            return {"Authorization": f"Basic {token}"}
        return {}
    
    def is_configured(self) -> bool:
        """Check if Jira is properly configured."""
        return bool(self.base_url and self.username and self.api_token and self.enabled)
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to Jira API."""
        if not self.is_configured():
            raise RuntimeError("Jira not configured")
        
        url = f"{self.base_url}/rest/api/3{endpoint}"
        headers = {
            **self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        response = await self.client.request(
            method, url, json=data, params=params, headers=headers
        )
        response.raise_for_status()
        return response.json() if response.text else {}
    
    # ========== Issue Operations ==========
    
    async def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """Get issue details by key.
        
        Args:
            issue_key: Issue key (e.g., "PROJ-123")
            
        Returns:
            Issue details including fields, status, assignee
        """
        logger.info(f"Fetching issue: {issue_key}")
        return await self._request("GET", f"/issue/{issue_key}")
    
    async def search_issues(
        self,
        jql: str,
        max_results: int = 50,
        start_at: int = 0,
        fields: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Search issues using JQL.
        
        Args:
            jql: JQL query string
            max_results: Maximum results to return
            start_at: Pagination offset
            fields: Specific fields to return
            
        Returns:
            Search results with issues list and total count
        """
        # Validate JQL for security
        if not self._validate_jql(jql):
            raise ValueError(f"Invalid JQL query: potentially unsafe pattern detected")
        
        logger.info(f"Searching issues with JQL: {jql[:100]}...")
        
        params = {
            "jql": jql,
            "maxResults": max_results,
            "startAt": start_at,
        }
        
        if fields:
            params["fields"] = ",".join(fields)
        
        return await self._request("GET", "/search", params=params)
    
    async def create_issue(
        self,
        project: str,
        summary: str,
        description: str,
        issue_type: str = "Task",
        priority: Optional[str] = None,
        assignee: Optional[str] = None,
        labels: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Create a new issue.
        
        Args:
            project: Project key (e.g., "PROJ")
            summary: Issue summary/title
            description: Issue description (supports ADF format)
            issue_type: Issue type (Task, Bug, Story, etc.)
            priority: Priority name
            assignee: Assignee account ID or email
            labels: List of labels
            
        Returns:
            Created issue key and details
        """
        logger.info(f"Creating issue in {project}: {summary[:50]}...")
        
        fields = {
            "project": {"key": project},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}]
                    }
                ]
            },
            "issuetype": {"name": issue_type},
        }
        
        if priority:
            fields["priority"] = {"name": priority}
        if assignee:
            fields["assignee"] = {"id": assignee} if len(assignee) == 36 else {"name": assignee}
        if labels:
            fields["labels"] = labels
        
        return await self._request("POST", "/issue", data={"fields": fields})
    
    async def update_issue(
        self,
        issue_key: str,
        summary: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        labels: Optional[List[str]] = None
    ) -> bool:
        """Update an existing issue.
        
        Args:
            issue_key: Issue key to update
            summary: New summary (optional)
            description: New description (optional)
            priority: New priority (optional)
            labels: New labels list (optional)
            
        Returns:
            True if update successful
        """
        logger.info(f"Updating issue: {issue_key}")
        
        fields = {}
        update = {}
        
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}]
                    }
                ]
            }
        if priority is not None:
            fields["priority"] = {"name": priority}
        if labels is not None:
            fields["labels"] = labels
        
        data = {}
        if fields:
            data["fields"] = fields
        
        if data:
            await self._request("PUT", f"/issue/{issue_key}", data=data)
        
        return True
    
    async def add_comment(self, issue_key: str, comment: str) -> Dict[str, Any]:
        """Add a comment to an issue.
        
        Args:
            issue_key: Issue key
            comment: Comment text
            
        Returns:
            Created comment details
        """
        logger.info(f"Adding comment to {issue_key}")
        
        body = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": comment}]
                }
            ]
        }
        
        return await self._request(
            "POST",
            f"/issue/{issue_key}/comment",
            data={"body": body}
        )
    
    async def get_comments(self, issue_key: str) -> List[Dict[str, Any]]:
        """Get all comments for an issue.
        
        Args:
            issue_key: Issue key
            
        Returns:
            List of comments with id, body, author, and created time
        """
        logger.info(f"Getting comments for {issue_key}")
        
        result = await self._request("GET", f"/issue/{issue_key}/comment")
        comments = result.get("comments", [])
        
        # Return simplified comment structure
        return [
            {
                "id": str(c.get("id", "")),
                "body": self._parse_adf_body(c.get("body", {})),
                "author": c.get("author", {}).get("displayName", "unknown"),
                "created": c.get("created", ""),
            }
            for c in comments
        ]
    
    def _parse_adf_body(self, body) -> str:
        """Extract text from Atlassian Document Format."""
        if isinstance(body, str):
            return body
        if not isinstance(body, dict):
            return ""
        content = body.get("content", [])
        if not content:
            return ""
        text_parts = []
        for block in content:
            self._extract_text(block, text_parts)
        return "".join(text_parts)
    
    def _extract_text(self, block, text_parts):
        """Recursively extract text from ADF block."""
        if not block:
            return
        block_type = block.get("type", "")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type in ("paragraph", "heading"):
            for item in block.get("content", []):
                self._extract_text(item, text_parts)
            text_parts.append("\n")
    
    async def get_transitions(self, issue_key: str) -> List[Dict[str, Any]]:
        """Get available transitions for an issue.
        
        Args:
            issue_key: Issue key
            
        Returns:
            List of available transitions with IDs and names
        """
        logger.info(f"Getting transitions for {issue_key}")
        
        result = await self._request("GET", f"/issue/{issue_key}/transitions")
        return result.get("transitions", [])
    
    async def transition_issue(
        self,
        issue_key: str,
        transition_id: str,
        comment: Optional[str] = None
    ) -> bool:
        """Transition an issue to a new status.
        
        Args:
            issue_key: Issue key
            transition_id: Transition ID from get_transitions()
            comment: Optional comment for the transition
            
        Returns:
            True if transition successful
        """
        logger.info(f"Transitioning {issue_key} with transition {transition_id}")
        
        data = {"transition": {"id": transition_id}}
        
        if comment:
            data["comment"] = [
                {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": comment}]}
                    ]
                }
            ]
        
        await self._request("POST", f"/issue/{issue_key}/transitions", data=data)
        return True
    
    async def get_issue_status(self, issue_key: str) -> str:
        """Get the current status of an issue.
        
        Args:
            issue_key: Issue key
            
        Returns:
            Status name (e.g., "To Do", "In Progress", "Done")
        """
        issue = await self.get_issue(issue_key)
        return issue.get("fields", {}).get("status", {}).get("name", "Unknown")
    
    async def assign_issue(self, issue_key: str, assignee: str) -> bool:
        """Assign an issue to a user.
        
        Args:
            issue_key: Issue key
            assignee: Account ID, email, or "-1" for unassigned
            
        Returns:
            True if assignment successful
        """
        logger.info(f"Assigning {issue_key} to {assignee}")
        
        data = {"accountId": assignee} if assignee != "-1" else None
        await self._request("PUT", f"/issue/{issue_key}/assignee", data=data)
        return True
    
    # ========== Utility Methods ==========
    
    def _validate_jql(self, jql: str) -> bool:
        """Validate JQL to prevent injection attacks."""
        jql_lower = jql.lower()
        for pattern in JQL_DANGEROUS_PATTERNS:
            if re.search(pattern, jql_lower):
                logger.warning(f"Potential JQL injection detected: {jql}")
                return False
        return True
    
    async def get_my_issues(self, status: Optional[str] = None) -> List[Dict]:
        """Get issues assigned to current user.
        
        Args:
            status: Optional status filter
            
        Returns:
            List of issues
        """
        jql = "assignee = currentUser()"
        if status:
            jql += f' AND status = "{status}"'
        jql += " ORDER BY updated DESC"
        
        result = await self.search_issues(jql, max_results=20)
        return result.get("issues", [])
    
    async def get_project_issues(self, project: str = None, status: str = None) -> List[Dict]:
        """Get issues in a project.
        
        Args:
            project: Project key (uses config default if not specified)
            status: Optional status filter
            
        Returns:
            List of issues
        """
        proj = project or self.project
        jql = f'project = "{proj}"'
        if status:
            jql += f' AND status = "{status}"'
        jql += " ORDER BY updated DESC"
        
        result = await self.search_issues(jql, max_results=50)
        return result.get("issues", [])
    
    async def start_session(self):
        """Start/initialize the Jira session.
        
        This method validates the configuration and ensures the client is ready.
        The HTTP client is already initialized in __init__, so this is primarily
        for validation and any setup needed.
        """
        if not self.is_configured():
            raise RuntimeError("Jira is not configured. Check your config.yaml")
        
        # Validate connection by making a lightweight request
        try:
            await self._request("GET", "/myself")
            logger.info("Jira session started and validated")
        except Exception as e:
            logger.warning(f"Jira session validation failed: {e}")
            raise
    
    async def close_session(self):
        """Close the Jira session and HTTP client."""
        await self.close()
        logger.info("Jira session closed")
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


# Global channel instance
jira_channel = JiraChannel()


# ========== Tool Functions for Agent ==========

async def jira_get_issue(issue_key: str) -> str:
    """Get details for a Jira issue."""
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    try:
        issue = await jira_channel.get_issue(issue_key)
        fields = issue.get("fields", {})
        
        status = fields.get("status", {}).get("name", "Unknown")
        assignee = fields.get("assignee", {})
        assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        summary = fields.get("summary", "")
        description = _parse_adf_body(fields.get("description", ""))
        
        return f"""**{issue_key}: {summary}**

**Status:** {status}
**Assignee:** {assignee}
**Priority:** {fields.get("priority", {}).get("name", "None")}
**Type:** {fields.get("issuetype", {}).get("name", "Task")}
**Created:** {fields.get("created", "")[:10]}
**Updated:** {fields.get("updated", "")[:10]}

**Description:**
{description[:500]}{'...' if len(description) > 500 else ''}"""
    except Exception as e:
        return f"Error getting issue {issue_key}: {str(e)}"


async def jira_search(jql: str, max_results: int = 10) -> str:
    """Search Jira issues using JQL."""
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    try:
        result = await jira_channel.search_issues(jql, max_results=max_results)
        issues = result.get("issues", [])
        total = result.get("total", 0)
        
        if not issues:
            return f"No issues found for JQL: {jql}"
        
        lines = [f"**Search Results** ({total} total, showing {len(issues)}):\n"]
        
        for issue in issues:
            key = issue.get("key")
            fields = issue.get("fields", {})
            status = fields.get("status", {}).get("name", "?")
            summary = fields.get("summary", "")[:50]
            lines.append(f"- **{key}** [{status}] {summary}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching issues: {str(e)}"


async def jira_add_comment(issue_key: str, comment: str) -> str:
    """Add a comment to a Jira issue."""
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    try:
        result = await jira_channel.add_comment(issue_key, comment)
        comment_id = result.get("id", "unknown")
        return f"Comment added to {issue_key}: ID={comment_id}"
    except Exception as e:
        return f"Error adding comment: {str(e)}"


async def jira_create_issue(
    project: str,
    summary: str,
    description: str,
    issue_type: str = "Task",
    priority: str = None
) -> str:
    """Create a new Jira issue."""
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    try:
        proj = project or jira_channel.project
        result = await jira_channel.create_issue(
            project=proj,
            summary=summary,
            description=description,
            issue_type=issue_type,
            priority=priority
        )
        issue_key = result.get("key", "unknown")
        return f"Issue created: **{issue_key}**\nSummary: {summary[:50]}"
    except Exception as e:
        return f"Error creating issue: {str(e)}"


async def jira_transition(
    issue_key: str,
    to_status: str,
    comment: str = None
) -> str:
    """Transition an issue to a new status."""
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    try:
        transitions = await jira_channel.get_transitions(issue_key)
        
        # Find transition by status name
        transition_id = None
        for t in transitions:
            if t.get("name", "").lower() == to_status.lower():
                transition_id = t.get("id")
                break
        
        if not transition_id:
            available = [t.get("name") for t in transitions]
            return f"Cannot transition to '{to_status}'. Available: {', '.join(available)}"
        
        await jira_channel.transition_issue(issue_key, transition_id, comment)
        return f"{issue_key} transitioned to '{to_status}'"
    except Exception as e:
        return f"Error transitioning issue: {str(e)}"


async def jira_get_transitions(issue_key: str) -> str:
    """Get available transitions for an issue."""
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    try:
        transitions = await jira_channel.get_transitions(issue_key)
        
        if not transitions:
            return f"No transitions available for {issue_key}"
        
        lines = [f"**Available Transitions for {issue_key}:**\n"]
        for t in transitions:
            lines.append(f"- {t.get('name')} (ID: {t.get('id')})")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting transitions: {str(e)}"


# ========== ADF Parsing Utility ==========

def _parse_adf_body(body: Any) -> str:
    """Extract text from Atlassian Document Format (ADF)."""
    if isinstance(body, str):
        return body
    
    if not isinstance(body, dict):
        return ""
    
    content = body.get("content", [])
    if not content:
        return ""
    
    text_parts = []
    for block in content:
        _extract_text_from_block(block, text_parts)
    
    return "".join(text_parts)


def _extract_text_from_block(block: Any, text_parts: List[str]):
    """Recursively extract text from an ADF block."""
    if not block:
        return
    
    block_type = block.get("type", "")
    
    if block_type == "text":
        text_parts.append(block.get("text", ""))
    elif block_type == "emoji":
        text_parts.append(block.get("attrs", {}).get("shortName", ""))
    elif block_type in ("paragraph", "heading", "blockquote"):
        inner = block.get("content", [])
        for item in inner:
            _extract_text_from_block(item, text_parts)
        text_parts.append("\n")
    elif block_type == "bulletList":
        for item in block.get("content", []):
            _extract_text_from_block(item, text_parts)
    elif block_type == "listItem":
        text_parts.append("• ")
        inner = block.get("content", [])
        for item in inner:
            _extract_text_from_block(item, text_parts)
    elif block_type == "codeBlock":
        lang = block.get("attrs", {}).get("language", "")
        text_parts.append(f"\n```{lang}\n")
        inner = block.get("content", [])
        for item in inner:
            _extract_text_from_block(item, text_parts)
        text_parts.append("\n```\n")
