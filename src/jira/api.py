"""
Jira Channel - Full API support for Jira issue management.

Features:
- Get, create, update, transition issues
- JQL search with validation
- Comment management
- Support for both Jira REST API v2 and v3
- Webhook handling for inbound events
"""

import base64
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.utils.truncate import truncate, truncate_json

from src.config import config
from src.utils.attachment import download_and_process_attachment

logger = logging.getLogger(__name__)

# Debug mode flag
_DEBUG_MODE = os.environ.get("DEBUG_JIRA", "").lower() in ("1", "true", "yes")


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled."""
    return _DEBUG_MODE or logger.isEnabledFor(logging.DEBUG)


def _truncate_json(data: Any, max_length: int = 500) -> str:
    """Truncate JSON for logging (wrapper for truncate_json)."""
    return truncate_json(data, max_length)


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
    """Jira channel adapter with REST API v2/v3 support."""
    
    # Valid API versions
    VALID_API_VERSIONS = ("2", "3")
    
    def __init__(self):
        self.enabled = config.jira.get("enabled", False)
        self.instances = config.get_jira_instances()
        
        # Initialize default client with first instance
        if self.instances:
            first = self.instances[0]
            self._init_client(first)
        else:
            self.base_url = ""
            self.username = ""
            self.password = ""
            self.token = ""  # Bearer token
            self.project = ""
            self.api_version = "2"
            self.timeout = 30.0
            self.client = httpx.AsyncClient(timeout=self.timeout)
            self._auth_header = {}
            self._auth_type = "None"
        
        logger.info(f"JiraChannel initialized with {len(self.instances)} instance(s)")
    
    def _init_client(self, instance: Dict[str, Any]):
        """Initialize client for a specific instance."""
        self.base_url = instance.get("url", "").rstrip("/")
        self.username = instance.get("username", "")
        self.password = instance.get("password", "")
        self.token = instance.get("token", "")  # Bearer token
        self.project = instance.get("project", "")
        
        # API version with validation
        api_version = str(instance.get("api_version", "2"))
        if api_version not in self.VALID_API_VERSIONS:
            logger.warning(f"Invalid api_version '{api_version}', defaulting to '2'")
            api_version = "2"
        self.api_version = api_version
        
        # Timeout
        self.timeout = float(instance.get("timeout", 30.0))
        
        self.client = httpx.AsyncClient(timeout=self.timeout)
        self._auth_header = self._get_auth_header()
        self._auth_type = self._get_auth_type()
    
    def get_instance_client(self, url: str = None, name: str = None) -> 'JiraChannel':
        """Get a JiraChannel client for a specific instance.
        
        Args:
            url: URL to match (e.g., from issue key like PROJ-123 from https://company.atlassian.net...)
            name: Instance name to match
            
        Returns:
            JiraChannel configured for the matched instance
        """
        instance = config.find_jira_instance(url=url, name=name)
        
        if not instance:
            logger.warning(f"No Jira instance found for url={url}, name={name}, using default")
            return self
        
        # Create new channel for this instance
        new_channel = JiraChannel()
        new_channel.enabled = self.enabled
        new_channel.instances = self.instances
        new_channel._init_client(instance)
        
        logger.info(f"Using Jira instance: {instance.get('name')} - {instance.get('url')}")
        return new_channel
    
    def reinit(self):
        """Reinitialize JiraChannel (called when config changes)."""
        logger.info("Reinitializing JiraChannel...")
        self.enabled = config.jira.get("enabled", False)
        self.instances = config.get_jira_instances()
        
        if self.instances:
            self._init_client(self.instances[0])
        else:
            self.base_url = ""
            self.username = ""
            self.password = ""
            self.token = ""
            self.project = ""
            self.client = httpx.AsyncClient(timeout=30.0)
            self._auth_header = {}
            self._auth_type = "None"
        
        logger.info(f"JiraChannel reinitialized with {len(self.instances)} instance(s)")
    
    def _get_auth_type(self) -> str:
        """Determine authentication type based on configuration."""
        # For Atlassian Cloud, use Basic Auth with username:api_token
        if self.username and self.token:
            return "Basic"
        elif self.token:
            return "Bearer"
        elif self.username and self.password:
            return "Basic"
        return "None"
    
    def _get_auth_header(self) -> Dict[str, str]:
        """Get authorization header based on authentication type."""
        # Basic Auth (username:token) - for Atlassian Cloud
        if self.username and self.token:
            creds = f"{self.username}:{self.token}"
            encoded = base64.b64encode(creds.encode()).decode()
            logger.debug("Using Basic Auth (email:api_token) for Cloud")
            return {"Authorization": f"Basic {encoded}"}
        
        # Bearer Token authentication
        if self.token:
            logger.debug("Using Bearer Token authentication")
            return {"Authorization": f"Bearer {self.token}"}
        
        # Basic Auth (username:password)
        if self.username and self.password:
            creds = f"{self.username}:{self.password}"
            token = base64.b64encode(creds.encode()).decode()
            logger.debug("Using Basic Auth authentication")
            return {"Authorization": f"Basic {token}"}
        
        logger.warning("No authentication credentials configured")
        return {}
    
    def is_configured(self) -> bool:
        """Check if Jira is properly configured with required credentials.
        
        Supports:
        - Bearer token: Authorization: Bearer {token}
        - Basic auth (username+password): Authorization: Basic {base64(username:password)}
        
        Note: This only checks if credentials are present, not if enabled.
        Use is_enabled() to check if the channel should be active.
        """
        has_auth = bool(
            (self.base_url) and
            (self.token or (self.username and self.password))
        )
        return has_auth
    
    def is_enabled(self) -> bool:
        """Check if Jira channel is enabled."""
        return bool(self.enabled)
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        files: Optional[Dict] = None,
        headers: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to Jira API with debug logging."""
        if not self.is_configured():
            raise RuntimeError("Jira not configured")
        
        # Build URL
        url = f"{self.base_url}/rest/api/{self.api_version}{endpoint}"
        
        # Debug: Log request
        if _is_debug_enabled():
            logger.debug(f"=== [JIRA] REQUEST ===")
            logger.debug(f"Method: {method}")
            logger.debug(f"URL: {url}")
            if params:
                logger.debug(f"Params: {json.dumps(params)}")
            if data:
                logger.debug(f"Data: {_truncate_json(data)}")
            if files:
                logger.debug(f"Files: {list(files.keys())}")
        
        # Default headers
        default_headers = {
            "Accept": "application/json"
        }
        
        # For file uploads, don't set Content-Type (httpx will set multipart boundary)
        if files:
            # For attachments, use X-Atlassian-Token header
            req_headers = {**default_headers, **(headers or {})}
            response = await self.client.request(
                method, url, files=files, params=params, headers=req_headers
            )
        else:
            req_headers = {
                **default_headers,
                **self._auth_header,
                "Content-Type": "application/json"
            }
            response = await self.client.request(
                method, url, json=data, params=params, headers=req_headers
            )
        
        # Debug: Log response
        if _is_debug_enabled():
            logger.debug(f"=== [JIRA] RESPONSE ===")
            logger.debug(f"Status: {response.status_code}")
        
        response.raise_for_status()
        result = response.json() if response.text else {}
        
        # Debug: Log response body
        if _is_debug_enabled():
            logger.debug(f"Body: {_truncate_json(result)}")
        
        return result
    
    # ========== Issue Operations ==========
    
    async def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """Get issue details by key.
        
        Args:
            issue_key: Issue key (e.g., "PROJ-123")
            
        Returns:
            Issue details including fields, status, assignee
        """
        logger.info(f"Fetching issue: {issue_key}")
        result = await self._request("GET", f"/issue/{issue_key}")
        
        # Debug: Log issue summary
        if _DEBUG_MODE or logger.isEnabledFor(logging.DEBUG):
            summary = result.get("fields", {}).get("summary", "No summary")
            status = result.get("fields", {}).get("status", {}).get("name", "Unknown")
            logger.debug(f"Issue {issue_key}: {status} - {summary}")
        
        return result
    
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
            fields: Specific fields to return (v2: limited support)
            
        Returns:
            Search results with issues list and total count
        """
        # Validate JQL for security
        if not self._validate_jql(jql):
            raise ValueError(f"Invalid JQL query: potentially unsafe pattern detected")
        
        logger.info(f"Searching issues with JQL: {truncate(jql, 100)}")
        
        params = {
            "jql": jql,
            "maxResults": max_results,
            "startAt": start_at,
        }
        
        # v2 has limited fields support
        if fields and self.api_version == "3":
            params["fields"] = ",".join(fields)
        
        return await self._request("GET", "/search/jql", params=params)
    
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
            description: Issue description (v2: plain text, v3: ADF format)
            issue_type: Issue type (Task, Bug, Story, etc.)
            priority: Priority name (v2: limited support)
            assignee: Assignee account ID or email
            labels: List of labels (v2: limited support)
            
        Returns:
            Created issue key and details
        """
        logger.info(f"Creating issue in {project}: {truncate(summary, 50)}")
        
        if self.api_version == "3":
            # v3: Use Atlassian Document Format (ADF)
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
        else:
            # v2: Use plain text for description
            fields = {
                "project": {"key": project},
                "summary": summary,
                "description": description,
                "issuetype": {"name": issue_type},
            }
        
        if priority and self.api_version == "3":
            fields["priority"] = {"name": priority}
        if assignee:
            # v2 may require different assignee format
            fields["assignee"] = {"id": assignee} if len(assignee) == 36 else {"name": assignee}
        if labels and self.api_version == "3":
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
            priority: New priority (optional, v3 only)
            labels: New labels list (optional, v3 only)
            
        Returns:
            True if update successful
        """
        logger.info(f"Updating issue: {issue_key}")
        
        fields = {}
        data = {}
        
        if summary is not None:
            fields["summary"] = summary
        
        if description is not None:
            if self.api_version == "3":
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
            else:
                fields["description"] = description
        
        if priority is not None and self.api_version == "3":
            fields["priority"] = {"name": priority}
        
        if labels is not None and self.api_version == "3":
            fields["labels"] = labels
        
        if fields:
            data["fields"] = fields
        
        if data:
            try:
                await self._request("PUT", f"/issue/{issue_key}", data=data)
                return True
            except Exception:
                logger.warning(f"Failed to update issue: {issue_key}")
                return False
        
        return True
    
    async def add_comment(self, issue_key: str, comment: str) -> Dict[str, Any]:
        """Add a comment to an issue.
        
        Args:
            issue_key: Issue key
            comment: Comment text (v2: plain text, v3: ADF format)
            
        Returns:
            Created comment details
        """
        logger.info(f"Adding comment to {issue_key}")
        
        if self.api_version == "3":
            # v3: Use ADF format
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
        else:
            # v2: Use plain text
            body = comment
        
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
                "body": self._parse_body(c.get("body", {})),
                "author": c.get("author", {}).get("displayName", "unknown"),
                "created": c.get("created", ""),
            }
            for c in comments
        ]

    def _parse_body(self, body) -> str:
        """Extract text from comment body (supports v2 plain text and v3 ADF)."""
        if isinstance(body, str):
            return body
        if not isinstance(body, dict):
            return ""
        return self._parse_adf_body(body)
    
    def _parse_adf_body(self, body) -> str:
        """Extract text from Atlassian Document Format (v3)."""
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
            if self.api_version == "3":
                data["comment"] = [
                    {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": comment}]}
                        ]
                    }
                ]
            else:
                data["comment"] = comment
        
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
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def add_attachment(self, issue_key: str, file_path: str) -> Dict[str, Any]:
        """Add an attachment to an issue.
        
        Args:
            issue_key: Issue key (e.g., "PROJ-123")
            file_path: Path to local file to upload
            
        Returns:
            Attachment details
        """
        import os
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}
        
        filename = os.path.basename(file_path)
        url = f"{self.base_url}/rest/api/{self.api_version}/issue/{issue_key}/attachments"
        
        # Build headers - include auth but NOT Content-Type (httpx will set multipart)
        headers = {
            **self._auth_header,
            "X-Atlassian-Token": "no-check",
            "Accept": "application/json"
        }
        
        # Use multipart form upload
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f)}
            response = await self.client.request(
                "POST", url, files=files, headers=headers
            )
        
        response.raise_for_status()
        return response.json() if response.text else []


# Global channel instance
jira_channel = JiraChannel()

# Register for config reload
from src.config import service_reload_manager
service_reload_manager.register('jira', jira_channel.reinit)


# ========== Tool Functions for Agent ==========


async def _process_issue_attachments(issue_key: str, fields: dict) -> str:
    """Process issue attachments and return them for LLM."""
    attachments = fields.get("attachment", [])
    if not attachments:
        return ""
    
    logger.info(f"Processing {len(attachments)} attachments for {issue_key}")
    
    results = []
    for i, att in enumerate(attachments[:5]):  # Max 5 attachments
        filename = att.get("filename", "unknown")
        mime_type = att.get("mimeType", "application/octet-stream")
        size = att.get("size", 0)
        
        content_url = att.get("content", "")
        
        if content_url:
            try:
                # Get auth header from Jira channel
                auth_header = jira_channel._auth_header if jira_channel.is_configured() else None
                
                result = await download_and_process_attachment(
                    url=content_url,
                    session_id=f"jira-{issue_key}",
                    options={"include_image_data": True},
                    auth_header=auth_header
                )
                
                if result.content_format == "base64":
                    results.append(f"- **{filename}** (image, {size} bytes)")
                    results.append(f"  {result.content}")
                elif result.content_format == "text" and result.content:
                    preview = result.content[:500]
                    results.append(f"- **{filename}** (text, {size} bytes)")
                    results.append(f"  {preview}")
                else:
                    results.append(f"- **{filename}** ({mime_type}, {size} bytes)")
            except Exception as e:
                logger.warning(f"Failed to process attachment {filename}: {e}")
                results.append(f"- **{filename}** ({mime_type}, {size} bytes) - [processing failed]")
        else:
            results.append(f"- **{filename}** ({mime_type}, {size} bytes)")
    
    if results:
        return "**Attachments:**\n" + "\n".join(results) + "\n"
    return ""


async def jira_get_issue(issue_key: str) -> str:
    """Get details for a Jira issue."""
    logger.debug(f"jira_get_issue called: {issue_key}")
    
    if not jira_channel.is_configured():
        logger.warning("jira_get_issue: Jira not configured")
        return "Error: Jira not configured"
    
    try:
        logger.info(f"Fetching issue: {issue_key}")
        issue = await jira_channel.get_issue(issue_key)
        fields = issue.get("fields", {})
        
        status = fields.get("status", {}).get("name", "Unknown")
        assignee = fields.get("assignee", {})
        assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        summary = fields.get("summary", "")
        description = jira_channel._parse_body(fields.get("description", ""))
        
        logger.debug(f"jira_get_issue: {issue_key} found, status={status}")
        
        # Process attachments
        attachment_info = await _process_issue_attachments(issue_key, fields)
        
        return f"""**{issue_key}: {summary}**

**Status:** {status}
**Assignee:** {assignee}
**Priority:** {fields.get("priority", {}).get("name", "None") if jira_channel.api_version == "3" else "N/A"}
**Type:** {fields.get("issuetype", {}).get("name", "Task")}
**Created:** {fields.get("created", "")[:10]}
**Updated:** {fields.get("updated", "")[:10]}
{attachment_info}
**Description:**
{description}"""
    except httpx.HTTPStatusError as e:
        logger.error(f"jira_get_issue: HTTP error {e.response.status_code} for {issue_key}")
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.exception(f"jira_get_issue: Failed to fetch {issue_key}")
        return f"Error getting issue {issue_key}: {str(e)}"


async def jira_search(jql: str, max_results: int = 10) -> str:
    """Search Jira issues using JQL."""
    logger.debug(f"jira_search called: jql={truncate(jql, 50)}, max_results={max_results}")
    
    if not jira_channel.is_configured():
        logger.warning("jira_search: Jira not configured")
        return "Error: Jira not configured"
    
    try:
        logger.info(f"Searching issues with JQL: {truncate(jql, 80)}")
        result = await jira_channel.search_issues(jql, max_results=max_results)
        issues = result.get("issues", [])
        total = result.get("total", 0)
        
        logger.debug(f"jira_search: found {total} issues, returning {len(issues)}")
        
        if not issues:
            return f"No issues found for JQL: {jql}"
        
        lines = [f"**Search Results** ({total} total, showing {len(issues)}):\n"]
        
        for issue in issues:
            key = issue.get("key")
            fields = issue.get("fields", {})
            status = fields.get("status", {}).get("name", "?")
            summary = fields.get("summary", "")
            lines.append(f"- **{key}** [{status}] {summary}")
        
        return "\n".join(lines)
    except ValueError as e:
        logger.warning(f"jira_search: Invalid JQL - {e}")
        return f"Error: Invalid JQL query - {str(e)}"
    except httpx.HTTPStatusError as e:
        logger.error(f"jira_search: HTTP error {e.response.status_code}")
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.exception("jira_search: Search failed")
        return f"Error searching issues: {str(e)}"


async def jira_add_comment(issue_key: str, comment: str) -> str:
    """Add a comment to a Jira issue."""
    logger.debug(f"jira_add_comment: {issue_key}, comment_len={len(comment)}")
    
    if not jira_channel.is_configured():
        logger.warning("jira_add_comment: Jira not configured")
        return "Error: Jira not configured"
    
    try:
        logger.info(f"Adding comment to {issue_key}")
        result = await jira_channel.add_comment(issue_key, comment)
        comment_id = result.get("id", "unknown")
        logger.info(f"Comment added: {issue_key}, comment_id={comment_id}")
        return f"Comment added to {issue_key}: ID={comment_id}"
    except httpx.HTTPStatusError as e:
        logger.error(f"jira_add_comment: HTTP error {e.response.status_code} for {issue_key}")
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.exception(f"jira_add_comment: Failed for {issue_key}")
        return f"Error adding comment: {str(e)}"


async def jira_create_issue(
    project: str,
    summary: str,
    description: str,
    issue_type: str = "Task",
    priority: str = None
) -> str:
    """Create a new Jira issue."""
    logger.debug(f"jira_create_issue: project={project}, summary={truncate(summary, 30)}")
    
    if not jira_channel.is_configured():
        logger.warning("jira_create_issue: Jira not configured")
        return "Error: Jira not configured"
    
    try:
        proj = project or jira_channel.project
        logger.info(f"Creating issue in {proj}: {truncate(summary, 50)}")
        result = await jira_channel.create_issue(
            project=proj,
            summary=summary,
            description=description,
            issue_type=issue_type,
            priority=priority
        )
        issue_key = result.get("key", "unknown")
        logger.info(f"Issue created: {issue_key}")
        return f"Issue created: **{issue_key}**\nSummary: {summary}"
    except httpx.HTTPStatusError as e:
        logger.error(f"jira_create_issue: HTTP error {e.response.status_code}")
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.exception("jira_create_issue: Failed to create issue")
        return f"Error creating issue: {str(e)}"


async def jira_transition(
    issue_key: str,
    to_status: str,
    comment: str = None
) -> str:
    """Transition an issue to a new status."""
    logger.debug(f"jira_transition: {issue_key} -> {to_status}")
    
    if not jira_channel.is_configured():
        logger.warning("jira_transition: Jira not configured")
        return "Error: Jira not configured"
    
    try:
        logger.info(f"Transitioning {issue_key} to {to_status}")
        transitions = await jira_channel.get_transitions(issue_key)
        
        # Find transition by status name
        transition_id = None
        for t in transitions:
            if t.get("name", "").lower() == to_status.lower():
                transition_id = t.get("id")
                break
        
        if not transition_id:
            available = [t.get("name") for t in transitions]
            logger.warning(f"jira_transition: No transition to '{to_status}', available: {available}")
            return f"Cannot transition to '{to_status}'. Available: {', '.join(available)}"
        
        await jira_channel.transition_issue(issue_key, transition_id, comment)
        logger.info(f"Issue transitioned: {issue_key} -> {to_status}")
        return f"{issue_key} transitioned to '{to_status}'"
    except httpx.HTTPStatusError as e:
        logger.error(f"jira_transition: HTTP error {e.response.status_code}")
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.exception(f"jira_transition: Failed for {issue_key}")
        return f"Error transitioning issue: {str(e)}"


async def jira_get_transitions(issue_key: str) -> str:
    """Get available transitions for an issue."""
    logger.debug(f"jira_get_transitions: {issue_key}")
    
    if not jira_channel.is_configured():
        logger.warning("jira_get_transitions: Jira not configured")
        return "Error: Jira not configured"
    
    try:
        logger.info(f"Getting transitions for {issue_key}")
        transitions = await jira_channel.get_transitions(issue_key)
        
        logger.debug(f"jira_get_transitions: {issue_key} has {len(transitions)} transitions")
        
        if not transitions:
            return f"No transitions available for {issue_key}"
        
        lines = [f"**Available Transitions for {issue_key}:**\n"]
        for t in transitions:
            lines.append(f"- {t.get('name')} (ID: {t.get('id')})")
        
        return "\n".join(lines)
    except httpx.HTTPStatusError as e:
        logger.error(f"jira_get_transitions: HTTP error {e.response.status_code}")
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.exception(f"jira_get_transitions: Failed for {issue_key}")
        return f"Error getting transitions: {str(e)}"


async def jira_get_comments(issue_key: str) -> str:
    """Get all comments for a Jira issue.
    
    This tool allows the model to retrieve and review comments on an issue,
    which is useful for understanding discussion history or context.
    
    Args:
        issue_key: Issue key (e.g., "PROJ-123")
        
    Returns:
        Formatted list of comments with author, date, and content
    """
    logger.debug(f"jira_get_comments: {issue_key}")
    
    if not jira_channel.is_configured():
        logger.warning("jira_get_comments: Jira not configured")
        return "Error: Jira not configured"
    
    try:
        logger.info(f"Getting comments for {issue_key}")
        comments = await jira_channel.get_comments(issue_key)
        
        logger.debug(f"jira_get_comments: {issue_key} has {len(comments)} comments")
        
        if not comments:
            return f"No comments found for {issue_key}"
        
        lines = [f"**Comments for {issue_key}** ({len(comments)} total):\n"]
        
        for i, comment in enumerate(comments, 1):
            author = comment.get("author", "Unknown")
            created = comment.get("created", "")[:10] if comment.get("created") else "N/A"
            body = comment.get("body", "")
            
            lines.append(f"---")
            lines.append(f"**Comment #{i}** by {author} on {created}")
            lines.append(f"{body}")
        
        return "\n".join(lines)
    except httpx.HTTPStatusError as e:
        logger.error(f"jira_get_comments: HTTP error {e.response.status_code}")
        return f"Error: HTTP {e.response.status_code} - {e.response.reason_phrase}"
    except Exception as e:
        logger.exception(f"jira_get_comments: Failed for {issue_key}")
        return f"Error getting comments: {str(e)}"


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


# ========== Tool Schemas for LLM ==========

def get_tools_schemas() -> list:
    """Return Jira tool schemas for OpenAI function calling."""
    return [
        {
            "type": "function",
            "function": {
                "name": "jira_get_issue",
                "description": "Get details for a Jira issue by key. Returns issue summary, status, assignee, and description.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_search",
                "description": "Search Jira issues using JQL (Jira Query Language). Returns a list of matching issues.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "jql": {"type": "string", "description": "JQL query string (e.g., 'project = PROJ AND status = Done')"},
                        "max_results": {"type": "integer", "description": "Maximum number of results to return", "default": 10}
                    },
                    "required": ["jql"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_add_comment",
                "description": "Add a comment to a Jira issue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "comment": {"type": "string", "description": "Comment text to add"}
                    },
                    "required": ["issue_key", "comment"]
                }
            }
        },
        # NOTE: jira_create_issue, jira_transition, jira_get_transitions, jira_get_comments
        # functions exist but schemas need to be added - adding below
        {
            "type": "function",
            "function": {
                "name": "jira_create_issue",
                "description": "Create a new Jira issue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_key": {"type": "string", "description": "Project key (e.g., PROJ)"},
                        "issue_type": {"type": "string", "description": "Issue type (e.g., Bug, Task, Story)", "default": "Task"},
                        "summary": {"type": "string", "description": "Issue summary/title"},
                        "description": {"type": "string", "description": "Issue description"}
                    },
                    "required": ["project_key", "summary"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_transition",
                "description": "Transition a Jira issue to a new status (e.g., Done, In Progress).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "to_status": {"type": "string", "description": "Target status name (e.g., 'Done', 'In Progress')"}
                    },
                    "required": ["issue_key", "to_status"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_transitions",
                "description": "Get available transitions for a Jira issue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_comments",
                "description": "Get all comments on a Jira issue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_update_issue",
                "description": "Update a Jira issue's summary and/or description.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "summary": {"type": "string", "description": "New summary (optional)"},
                        "description": {"type": "string", "description": "New description (optional)"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_assign_issue",
                "description": "Assign a Jira issue to a user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "assignee": {"type": "string", "description": "Username or email to assign to"}
                    },
                    "required": ["issue_key", "assignee"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_projects",
                "description": "Get all accessible Jira projects.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_components",
                "description": "Get all components for a Jira project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_key": {"type": "string", "description": "Project key (e.g., PROJ)"}
                    },
                    "required": ["project_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_versions",
                "description": "Get all versions for a Jira project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_key": {"type": "string", "description": "Project key (e.g., PROJ)"}
                    },
                    "required": ["project_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_worklog",
                "description": "Get work logs for a Jira issue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_add_worklog",
                "description": "Add work log to a Jira issue.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "time_spent": {"type": "string", "description": "Time spent (e.g., '2h 30m', '1w')"},
                        "comment": {"type": "string", "description": "Work log comment (optional)"}
                    },
                    "required": ["issue_key", "time_spent"]
                }
            }
        },
    ]


# ========== Additional Jira Tools ==========

async def jira_update_issue(issue_key: str, summary: str = None, description: str = None) -> str:
    """Update a Jira issue's summary and/or description."""
    try:
        data = {}
        if summary:
            data["fields"] = {"summary": summary}
        if description:
            if "fields" not in data:
                data["fields"] = {}
            data["fields"]["description"] = {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}]
            }
        
        if not data:
            return "Error: No fields to update"
        
        result = await jira_channel._request("PUT", f"/issue/{issue_key}", data=data)
        return f"Issue {issue_key} updated successfully"
    except Exception as e:
        return f"Error updating issue {issue_key}: {str(e)}"


async def jira_assign_issue(issue_key: str, assignee: str = None) -> str:
    """Assign a Jira issue to a user. Use empty string to unassign."""
    try:
        if assignee is None:
            return "Error: assignee parameter is required"
        
        # Get accountId from username if needed
        account_id = assignee
        if "@" not in assignee:
            # Search for user
            user_search = await jira_channel._request(
                "GET", 
                f"/user/search?query={assignee}"
            )
            if user_search and len(user_search) > 0:
                account_id = user_search[0].get("accountId", assignee)
        
        data = {"accountId": account_id} if account_id else None
        result = await jira_channel._request("PUT", f"/issue/{issue_key}/assignee", data=data)
        return f"Issue {issue_key} assigned to {assignee}"
    except Exception as e:
        return f"Error assigning issue {issue_key}: {str(e)}"


async def jira_get_projects() -> str:
    """Get all accessible Jira projects."""
    try:
        result = await jira_channel._request("GET", "/project")
        if not result:
            return "No projects found or not authorized"
        
        projects = []
        for p in result:
            projects.append(f"- {p.get('key')}: {p.get('name')} (id: {p.get('id')})")
        
        return "Available Projects:\n" + "\n".join(projects)
    except Exception as e:
        return f"Error fetching projects: {str(e)}"


async def jira_get_components(project_key: str) -> str:
    """Get all components for a Jira project."""
    try:
        result = await jira_channel._request("GET", f"/project/{project_key}/components")
        if not result:
            return f"No components found for project {project_key}"
        
        components = []
        for c in result:
            components.append(f"- {c.get('name')} (id: {c.get('id')})")
        
        return f"Components for {project_key}:\n" + "\n".join(components)
    except Exception as e:
        return f"Error fetching components: {str(e)}"


async def jira_get_versions(project_key: str) -> str:
    """Get all versions for a Jira project."""
    try:
        result = await jira_channel._request("GET", f"/project/{project_key}/versions")
        if not result:
            return f"No versions found for project {project_key}"
        
        versions = []
        for v in result:
            released = "released" if v.get("released") else "unreleased"
            versions.append(f"- {v.get('name')} ({released})")
        
        return f"Versions for {project_key}:\n" + "\n".join(versions)
    except Exception as e:
        return f"Error fetching versions: {str(e)}"


async def jira_get_worklog(issue_key: str) -> str:
    """Get work logs for a Jira issue."""
    try:
        result = await jira_channel._request("GET", f"/issue/{issue_key}/worklog")
        if not result or not result.get("worklogs"):
            return f"No work logs found for {issue_key}"
        
        logs = []
        for w in result["worklogs"]:
            author = w.get("author", {}).get("displayName", "Unknown")
            time_spent = w.get("timeSpent", 0)
            started = w.get("started", "")[:10]
            logs.append(f"- {author}: {time_spent}s on {started}")
        
        return f"Work logs for {issue_key}:\n" + "\n".join(logs)
    except Exception as e:
        return f"Error fetching worklog: {str(e)}"


async def jira_add_worklog(issue_key: str, time_spent: str, comment: str = None) -> str:
    """Add work log to a Jira issue. time_spent format: '2h 30m' or '2w' etc."""
    try:
        data = {"timeSpent": time_spent}
        if comment:
            data["comment"] = {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment}]}]
            }
        
        result = await jira_channel._request("POST", f"/issue/{issue_key}/worklog", data=data)
        return f"Work log added to {issue_key}: {time_spent}"
    except Exception as e:
        return f"Error adding worklog: {str(e)}"


async def jira_add_attachment(issue_key: str, file_path: str) -> str:
    """Add an attachment to a Jira issue.
    
    Args:
        issue_key: Jira issue key (e.g., "PROJ-123")
        file_path: Path to local file to upload
        
    Returns:
        Success message with attachment details
    """
    import os
    try:
        if not jira_channel.is_configured():
            return "Error: Jira is not configured."
        
        if not os.path.exists(file_path):
            return f"Error: File not found: {file_path}"
        
        result = await jira_channel.add_attachment(issue_key, file_path)
        
        if isinstance(result, dict) and "error" in result:
            return f"Error: {result['error']}"
        
        # Return success with attachment info
        if isinstance(result, list) and len(result) > 0:
            att = result[0]
            return f"Attachment added: {att.get('filename', 'unknown')} ({att.get('size', 0)} bytes)"
        return f"Attachment uploaded successfully"
    except Exception as e:
        logger.error(f"jira_add_attachment: {e}")
        return f"Error adding attachment: {e}"
