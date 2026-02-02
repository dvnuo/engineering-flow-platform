"""Jira channel adapter for OpenClaw Mini."""

import base64
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)

# JQL injection patterns to block
JQL_DANGEROUS_PATTERNS = [
    r";",           # Statement separator
    r"--",          # SQL comment
    r"/\*",         # Block comment start
    r"\*/",         # Block comment end
    r"xp_",         # Extended stored procedures
    r"exec\s",      # EXEC command
    r"execute\s",   # EXECUTE command
]


def validate_jql(jql: str) -> bool:
    """Validate JQL query to prevent injection attacks."""
    jql_lower = jql.lower()
    for pattern in JQL_DANGEROUS_PATTERNS:
        if re.search(pattern, jql_lower):
            logger.warning(f"Potential JQL injection detected: {jql}")
            return False
    return True


def parse_adf_body(body: Any) -> str:
    """Extract text from Atlassian Document Format (ADF)."""
    if isinstance(body, str):
        return body
    
    if not isinstance(body, dict):
        return ""
    
    # Handle ADF format
    content = body.get("content", [])
    if not content:
        return ""
    
    # Extract text from all blocks
    text_parts = []
    for block in content:
        if block.get("type") == "paragraph":
            paragraph_content = block.get("content", [])
            for item in paragraph_content:
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif item.get("type") == "emoji":
                    text_parts.append(item.get("attrs", {}).get("shortName", ""))
    
    return "".join(text_parts)


# Maximum comment length in Jira
JIRA_MAX_COMMENT_LENGTH = 32767


class JiraChannel:
    """Jira channel adapter for receiving and sending comments."""

    def __init__(self):
        self.base_url = config.jira.get("base_url", "").rstrip("/")
        self.email = config.jira.get("email", "")
        self.api_token = config.jira.get("api_token", "")
        self.project_key = config.jira.get("project_key", "")
        
        # Auth header
        auth_string = f"{self.email}:{self.api_token}"
        self.headers = {
            "Authorization": f"Basic {base64.b64encode(auth_string.encode()).decode()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.session: Optional[httpx.AsyncClient] = None

    async def start_session(self) -> None:
        """Start the HTTP session."""
        self.session = httpx.AsyncClient(timeout=30.0)
        logger.info(f"Jira channel initialized for {self.base_url}")

    async def close_session(self) -> None:
        """Close the HTTP session."""
        if self.session:
            await self.session.aclose()
            self.session = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make an authenticated request to Jira API."""
        url = f"{self.base_url}{endpoint}"
        
        response = await self.session.request(
            method, url, headers=self.headers, **kwargs
        )
        
        if response.status_code >= 400:
            error = response.text
            logger.error(f"Jira API error ({response.status_code}): {error}")
            raise Exception(f"Jira API error: {response.status_code}")
        
        return response.json()

    async def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """Get issue details."""
        return await self._request("GET", f"/rest/api/3/issue/{issue_key}")

    async def get_issue_comments(self, issue_key: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get comments for an issue."""
        params = {"maxResults": limit, "orderBy": "-created"}
        data = await self._request(
            "GET", f"/rest/api/3/issue/{issue_key}/comment", params=params
        )
        return data.get("comments", [])

    async def add_comment(self, issue_key: str, body: str) -> Dict[str, Any]:
        """Add a comment to an issue."""
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": body}],
                    }
                ],
            }
        }
        
        return await self._request(
            "POST", f"/rest/api/3/issue/{issue_key}/comment", json=payload
        )

    async def add_comment_text_only(self, issue_key: str, body: str) -> Dict[str, Any]:
        """Add a plain text comment to an issue."""
        payload = {"body": body}
        
        return await self._request(
            "POST", f"/rest/api/3/issue/{issue_key}/comment", json=payload
        )

    async def add_comment_code_block(
        self,
        issue_key: str,
        code: str,
        language: str = "python",
    ) -> Dict[str, Any]:
        """Add a comment with a code block in ADF format.
        
        Args:
            issue_key: The issue key (e.g., PROJ-123)
            code: The code to display
            language: Programming language for syntax highlighting
        """
        # Escape special ADF characters
        escaped_code = code.replace("{", "\\{").replace("}", "\\}")
        
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "测试用例代码："
                            }
                        ]
                    },
                    {
                        "type": "codeBlock",
                        "attrs": {
                            "language": language
                        },
                        "content": [
                            {
                                "type": "text",
                                "text": code
                            }
                        ]
                    }
                ],
            }
        }
        
        return await self._request(
            "POST", f"/rest/api/3/issue/{issue_key}/comment", json=payload
        )

    async def add_comment_long(
        self,
        issue_key: str,
        body: str,
        max_length: int = JIRA_MAX_COMMENT_LENGTH,
    ) -> List[Dict[str, Any]]:
        """Add a long comment by splitting into multiple comments if needed.
        
        Jira has a maximum comment length, so this method splits long messages.
        Returns a list of all created comment IDs.
        """
        if len(body) <= max_length:
            # Single comment
            result = await self.add_comment_text_only(issue_key, body)
            return [result.get("id", "")]
        
        # Split into multiple comments
        results = []
        for i in range(0, len(body), max_length):
            chunk = body[i : i + max_length]
            # Add continuation indicator
            if i > 0:
                chunk = f"(continued) {chunk}"
            if i + max_length < len(body):
                chunk = f"{chunk} ..."
            
            result = await self.add_comment_text_only(issue_key, chunk)
            results.append(result.get("id", ""))
        
        logger.info(
            f"Split long comment into {len(results)} parts for {issue_key}"
        )
        return results

    async def update_comment(self, issue_key: str, comment_id: str, body: str) -> Dict[str, Any]:
        """Update a comment."""
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": body}],
                    }
                ],
            }
        }
        
        return await self._request(
            "PUT",
            f"/rest/api/3/issue/{issue_key}/comment/{comment_id}",
            json=payload,
        )

    async def get_projects(self) -> List[Dict[str, Any]]:
        """Get accessible projects."""
        data = await self._request("GET", "/rest/api/3/project")
        return data if isinstance(data, list) else []

    async def search_issues(
        self,
        jql: str,
        limit: int = 50,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search issues using JQL with injection protection."""
        # Validate JQL to prevent injection
        if not validate_jql(jql):
            logger.error(f"Invalid JQL query rejected: {jql}")
            raise ValueError("Invalid JQL query: potentially dangerous characters detected")
        
        params = {
            "jql": jql,
            "maxResults": limit,
            "fields": fields or ["summary", "status", "assignee", "created"],
        }
        
        data = await self._request("GET", "/rest/api/3/search", params=params)
        return data.get("issues", [])

    def handle_webhook_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle an incoming Jira webhook payload.
        
        Supports:
        - issue_comment_created
        - issue_updated
        """
        webhook_event = payload.get("webhookEvent", {})
        event_name = webhook_event.get("name", "")
        
        # Only handle comment events
        if event_name != "issue_comment_created":
            return None

        issue = webhook_event.get("issue", {})
        comment = webhook_event.get("comment", {})
        
        # Extract author
        author = comment.get("author", {})
        username = author.get("displayName", author.get("accountId", "unknown"))

        # Get comment body (handle ADF format)
        body = parse_adf_body(comment.get("body", ""))
        
        # Extract issue key
        issue_key = issue.get("key", "")
        
        # Filter by project if configured
        if self.project_key and issue_key and not issue_key.startswith(self.project_key):
            return None
        
        return {
            "event_type": event_name,
            "issue_key": issue_key,
            "comment_id": comment.get("id"),
            "body": body,
            "username": username,
            "author_id": author.get("accountId"),
            "created": comment.get("created"),
            "raw": payload,
        }

    def create_session_id(self, issue_key: str) -> str:
        """Create a session ID for an issue."""
        return f"jira:{issue_key}"

    async def get_issue_description(self, issue_key: str) -> str:
        """Get issue description as requirements text.
        
        Parses ADF format if needed.
        """
        issue = await self.get_issue(issue_key)
        description = issue.get("fields", {}).get("description", "")
        return parse_adf_body(description)

    def is_test_case_command(self, comment_body: str) -> bool:
        """Check if comment is a test case generation command.
        
        Commands:
        - "@bot 创建测试用例"
        - "@bot 生成测试"
        - "@bot create test cases"
        """
        patterns = [
            r"创建测试用例",
            r"生成测试",
            r"create\s+test\s+cases?",
            r"generate\s+test",
            r"create\s+tests\b",
        ]
        return any(re.search(p, comment_body, re.IGNORECASE) for p in patterns)


# Global Jira channel instance
jira_channel = JiraChannel()


async def run_jira(message_callback=None):
    """Run the Jira channel adapter."""
    await jira_channel.start_session()
    logger.info("Jira channel adapter started")


async def stop_jira():
    """Stop the Jira channel adapter."""
    await jira_channel.close_session()
    logger.info("Jira channel adapter stopped")
