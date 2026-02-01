"""Jira channel adapter for OpenClaw Mini."""

import asyncio
import base64
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from openclaw_mini.config import config

logger = logging.getLogger(__name__)


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
        """Search issues using JQL."""
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
        body = comment.get("body", "")
        if isinstance(body, dict):
            # Extract text from ADF format
            content = body.get("content", [])
            if content:
                first_block = content[0]
                if first_block.get("type") == "paragraph":
                    text_content = first_block.get("content", [])
                    if text_content and text_content[0].get("type") == "text":
                        body = text_content[0].get("text", "")
        
        # Extract issue key
        issue_key = issue.get("key", "")
        
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
