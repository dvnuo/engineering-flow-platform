"""Discord channel adapter for OpenClaw Mini."""

import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web

from openclaw_mini.config import config

logger = logging.getLogger(__name__)


class DiscordChannel:
    """Discord channel adapter for receiving and sending messages."""

    def __init__(self):
        self.bot_token = config.discord.get("bot_token", "")
        self.channel_id = config.discord.get("channel_id", "")
        self.base_url = "https://discord.com/api/v10"
        self.headers = {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json",
        }
        self.session: Optional[aiohttp.ClientSession] = None

    async def start_session(self) -> None:
        """Start the aiohttp session."""
        self.session = aiohttp.ClientSession()

    async def close_session(self) -> None:
        """Close the aiohttp session."""
        if self.session:
            await self.session.close()
            self.session = None

    async def send_message(self, content: str, channel_id: Optional[str] = None) -> Dict[str, Any]:
        """Send a message to a Discord channel."""
        channel = channel_id or self.channel_id
        url = f"{self.base_url}/channels/{channel}/messages"

        payload = {"content": content}

        async with self.session.post(url, headers=self.headers, json=payload) as response:
            if response.status not in (200, 201):
                error = await response.text()
                logger.error(f"Failed to send message: {error}")
                raise Exception(f"Discord API error: {response.status}")
            return await response.json()

    async def get_channel_messages(
        self,
        channel_id: Optional[str] = None,
        limit: int = 10,
    ) -> list:
        """Get recent messages from a channel."""
        channel = channel_id or self.channel_id
        url = f"{self.base_url}/channels/{channel}/messages?limit={limit}"

        async with self.session.get(url, headers=self.headers) as response:
            response.raise_for_status()
            return await response.json()

    async def create_webhook(self, channel_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a webhook in a channel for receiving messages."""
        channel = channel_id or self.channel_id
        url = f"{self.base_url}/channels/{channel}/webhooks"

        payload = {"name": "openclaw-mini"}

        async with self.session.post(url, headers=self.headers, json=payload) as response:
            response.raise_for_status()
            return await response.json()

    async def delete_webhook(self, webhook_id: str, token: str) -> None:
        """Delete a webhook."""
        url = f"{self.base_url}/webhooks/{webhook_id}/{token}"

        async with self.session.delete(url) as response:
            response.raise_for_status()

    async def handle_webhook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an incoming webhook payload."""
        # Extract message information
        message_id = payload.get("id")
        channel_id = payload.get("channel_id")
        content = payload.get("content", "")
        author = payload.get("author", {})
        username = author.get("username", "unknown")

        # Extract thread info if present
        thread_id = None
        if payload.get("thread"):
            thread_id = payload["thread"].get("id")

        return {
            "message_id": message_id,
            "channel_id": channel_id,
            "content": content,
            "username": username,
            "thread_id": thread_id,
            "raw": payload,
        }

    async def send_thread_message(
        self,
        content: str,
        thread_id: str,
    ) -> Dict[str, Any]:
        """Send a message to a thread."""
        url = f"{self.base_url}/channels/{thread_id}/messages"

        payload = {"content": content}

        async with self.session.post(url, headers=self.headers, json=payload) as response:
            if response.status not in (200, 201):
                error = await response.text()
                logger.error(f"Failed to send thread message: {error}")
                raise Exception(f"Discord API error: {response.status}")
            return await response.json()


# Global Discord channel instance
discord_channel = DiscordChannel()
