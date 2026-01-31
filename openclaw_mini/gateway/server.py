"""Gateway server for OpenClaw Mini."""

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any, Dict

from aiohttp import web
from aiohttp.web import Request

from openclaw_mini.agent.core import agent
from openclaw_mini.channel.discord import discord_channel
from openclaw_mini.config import config
from openclaw_mini.session.manager import DISCORD_SESSION_PREFIX

logger = logging.getLogger(__name__)


def verify_discord_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify Discord webhook signature."""
    if not signature or not secret:
        return True  # Skip verification if secret not configured
    
    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    
    return hmac.compare_digest(signature, f"sha256={expected}")


class Gateway:
    """Simple HTTP/WebSocket gateway for OpenClaw Mini."""

    def __init__(self):
        self.host = config.server.get("host", "0.0.0.0")
        self.port = config.server.get("port", 8000)
        self.app = web.Application()
        self.runner: web.AppRunner = None
        self.site: web.TCPSite = None

        # Register routes
        self.app.router.add_post("/webhook/discord", self.handle_discord_webhook)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/api/sessions", self.handle_list_sessions)
        self.app.router.add_post("/api/sessions/{session_id}/clear", self.handle_clear_session)

    async def handle_health(self, request: Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"status": "ok", "service": "openclaw-mini"})

    async def handle_discord_webhook(self, request: Request) -> web.Response:
        """Handle Discord webhook events."""
        try:
            # Read raw body for signature verification
            body = await request.read()
            
            # Verify Discord signature
            signature = request.headers.get("X-Signature-SHA256", "")
            webhook_secret = config.discord.get("webhook_secret", "")
            
            if not verify_discord_signature(body, signature, webhook_secret):
                logger.warning("Invalid Discord webhook signature")
                return web.json_response({"status": "error", "message": "Invalid signature"}, status=401)

            payload = json.loads(body)

            # Handle different event types
            event_type = payload.get("type", 0)

            if event_type == 1:
                # Discord PING event - respond immediately
                return web.json_response({"type": 1})

            # Handle message events
            if event_type == 0:
                data = payload.get("d", {})

                # Skip bot messages
                if data.get("author", {}).get("bot", False):
                    return web.json_response({"status": "ignored", "reason": "bot_message"})

                # Extract message content
                content = data.get("content", "").strip()
                channel_id = data.get("channel_id")
                message_id = data.get("id")
                guild_id = data.get("guild_id", "")

                if not content:
                    return web.json_response({"status": "ignored", "reason": "empty_message"})

                # Create session ID with consistent prefix
                session_id = f"{DISCORD_SESSION_PREFIX}{guild_id}:{channel_id}"

                # Get username
                username = data.get("author", {}).get("username", "unknown")

                # Process message through agent
                try:
                    response = await agent.process(
                        message=content,
                        session_id=session_id,
                        user_name=username,
                    )

                    # Send response to Discord
                    await discord_channel.send_message(response, channel_id)

                    logger.info(f"Processed message {message_id} from {username}")

                    return web.json_response({
                        "status": "processed",
                        "message_id": message_id,
                    })

                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    await discord_channel.send_message(
                        f"Sorry, I encountered an error: {str(e)}",
                        channel_id,
                    )
                    return web.json_response({"status": "error", "message": str(e)}, status=500)

            return web.json_response({"status": "ok"})

        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def handle_list_sessions(self, request: Request) -> web.Response:
        """List all active sessions."""
        from openclaw_mini.session.manager import session_manager
        sessions = session_manager.list_sessions()
        return web.json_response({"sessions": sessions, "count": len(sessions)})

    async def handle_clear_session(self, request: Request) -> web.Response:
        """Clear a session's history."""
        from openclaw_mini.session.manager import session_manager
        session_id = request.match_info.get("session_id", "")

        if session_id:
            session_manager.clear_history(session_id)
            return web.json_response({"status": "cleared", "session_id": session_id})
        else:
            return web.json_response({"status": "error", "message": "session_id required"}, status=400)

    async def start(self) -> None:
        """Start the gateway server."""
        # Start Discord session
        await discord_channel.start_session()

        # Start HTTP server
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        logger.info(f"Gateway started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the gateway server."""
        # Stop HTTP server
        if self.runner:
            await self.runner.cleanup()

        # Close Discord session
        await discord_channel.close_session()

        logger.info("Gateway stopped")


# Global gateway instance
gateway = Gateway()
