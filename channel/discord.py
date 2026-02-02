"""Discord channel adapter for OpenClaw Mini - Bot API Mode."""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

import discord
from discord.ext import commands

from config import config

logger = logging.getLogger(__name__)


class DiscordBot(commands.Bot):
    """Discord Bot for receiving and sending messages via Bot API."""

    def __init__(self, message_callback: Optional[Callable] = None):
        # Get config
        token = config.discord.get("bot_token", "")
        self.target_channel_id = config.discord.get("channel_id", "")
        self.message_callback = message_callback

        # Initialize bot with intents
        intents = discord.Intents.default()
        intents.message_content = True  # Required for reading messages

        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def on_ready(self):
        """Called when bot is ready."""
        logger.info(f"Discord Bot logged in as {self.user} (ID: {self.user.id})")
        
        # Check if target channel is accessible
        if self.target_channel_id:
            try:
                channel = await self.fetch_channel(int(self.target_channel_id))
                logger.info(f"Connected to channel: #{channel.name}")
            except Exception as e:
                logger.warning(f"Could not access channel {self.target_channel_id}: {e}")

    async def on_message(self, message: discord.Message):
        """Called when a message is received."""
        # Ignore messages from bots (including self)
        if message.author.bot:
            return

        # Ignore messages not in target channel (if configured)
        if self.target_channel_id:
            if str(message.channel.id) != str(self.target_channel_id):
                # Check if it's a DM
                if isinstance(message.channel, discord.DMChannel):
                    pass  # Allow DMs
                else:
                    return

        # Extract message info
        content = message.content
        username = str(message.author)
        channel_id = str(message.channel.id)
        message_id = str(message.id)
        guild_id = str(message.guild.id) if message.guild else "dm"

        logger.info(f"Received message from {username} in channel {channel_id}: {content[:50]}...")

        # Call message callback if provided
        if self.message_callback:
            try:
                # Create session ID
                session_id = f"discord:{guild_id}:{channel_id}"

                # Call the callback to process message and get response
                response = await self.message_callback(
                    message=content,
                    session_id=session_id,
                    user_name=username,
                )

                # Send response
                if response:
                    await message.channel.send(response)
                    logger.info(f"Sent response to {username}")

            except Exception as e:
                logger.error(f"Error processing message: {e}")
                await message.channel.send(f"Sorry, I encountered an error: {str(e)}")

        # Process commands (for future command support)
        await self.process_commands(message)

    async def send_message(self, content: str, channel_id: Optional[str] = None) -> Dict[str, Any]:
        """Send a message to a Discord channel."""
        target_id = channel_id or self.target_channel_id
        if not target_id:
            raise ValueError("No channel ID specified")

        channel = await self.fetch_channel(int(target_id))
        sent_message = await channel.send(content)

        return {
            "id": str(sent_message.id),
            "content": content,
            "channel_id": target_id,
        }

    async def close(self):
        """Close the bot connection."""
        await super().close()
        logger.info("Discord Bot disconnected")


class DiscordChannel:
    """Discord channel adapter supporting both Webhook and Bot API modes."""

    def __init__(self):
        self.mode = config.discord.get("mode", "bot")  # 'bot' or 'webhook'
        self.bot_token = config.discord.get("bot_token", "")
        self.channel_id = config.discord.get("channel_id", "")
        self.webhook_url = config.discord.get("webhook_url", "")

        # Bot API mode components
        self.bot: Optional[DiscordBot] = None
        self._bot_task: Optional[asyncio.Task] = None

        # Webhook mode components
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = "https://discord.com/api/v10"
        self.headers = {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json",
        }

    def set_message_callback(self, callback: Callable):
        """Set the message callback for Bot API mode."""
        if self.mode == "bot":
            if self.bot:
                self.bot.message_callback = callback
        else:
            logger.warning("Message callback only works in Bot API mode")

    async def start(self, message_callback: Optional[Callable] = None) -> None:
        """Start the Discord channel adapter."""
        if self.mode == "bot":
            await self._start_bot_mode(message_callback)
        else:
            await self._start_webhook_mode()

    async def _start_bot_mode(self, message_callback: Optional[Callable] = None) -> None:
        """Start in Bot API mode using discord.py."""
        if not self.bot_token:
            raise ValueError("bot_token is required for Bot API mode")

        logger.info(f"Starting Discord Bot in mode='{self.mode}'...")

        self.bot = DiscordBot(message_callback=message_callback)
        
        # Run the bot
        try:
            await self.bot.start(self.bot_token)
        except KeyboardInterrupt:
            await self.bot.close()

    async def _start_webhook_mode(self) -> None:
        """Start in Webhook mode."""
        if not self.webhook_url:
            raise ValueError("webhook_url is required for Webhook mode")

        logger.info(f"Starting Discord Webhook in mode='{self.mode}'...")
        self.session = aiohttp.ClientSession()
        logger.info("Webhook mode ready (requires external HTTP server for webhook endpoint)")

    async def stop(self) -> None:
        """Stop the Discord channel adapter."""
        if self.mode == "bot" and self.bot:
            await self.bot.close()
            self.bot = None
        elif self.mode == "webhook" and self.session:
            await self.session.close()
            self.session = None

    # Webhook mode methods (for backward compatibility)
    async def send_message(self, content: str, channel_id: Optional[str] = None) -> Dict[str, Any]:
        """Send a message to a Discord channel."""
        if self.mode == "bot" and self.bot:
            # Bot API mode - use channel.send() directly
            target_id = channel_id or self.target_channel_id
            if not target_id:
                raise ValueError("No channel ID specified")
            
            channel = await self.bot.fetch_channel(int(target_id))
            sent_message = await channel.send(content)
            
            return {
                "id": str(sent_message.id),
                "content": content,
                "channel_id": target_id,
            }
        else:
            # Webhook mode - use HTTP API
            channel = channel_id or self.channel_id
            url = f"{self.base_url}/channels/{channel}/messages"
            payload = {"content": content}

            async with self.session.post(url, headers=self.headers, json=payload) as response:
                if response.status not in (200, 201):
                    error = await response.text()
                    logger.error(f"Failed to send message: {error}")
                    raise Exception(f"Discord API error: {response.status}")
                return await response.json()

    async def handle_webhook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle an incoming webhook payload."""
        message_id = payload.get("id")
        channel_id = payload.get("channel_id")
        content = payload.get("content", "")
        author = payload.get("author", {})
        username = author.get("username", "unknown")
        thread_id = payload.get("thread", {}).get("id") if payload.get("thread") else None

        return {
            "message_id": message_id,
            "channel_id": channel_id,
            "content": content,
            "username": username,
            "thread_id": thread_id,
            "raw": payload,
        }


# Global Discord channel instance
discord_channel = DiscordChannel()


async def run_bot(message_callback: Optional[Callable] = None):
    """Run the Discord bot with a message callback."""
    await discord_channel.start(message_callback=message_callback)


def setup_bot(message_callback: Callable):
    """Setup the Discord bot with a message callback."""
    discord_channel.set_message_callback(message_callback)
