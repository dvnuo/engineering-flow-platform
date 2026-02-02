"""Gateway server for OpenClaw Mini."""

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any, Callable, Dict

from aiohttp import web
from aiohttp.web import Request

from agent.core import agent
from channel.discord import discord_channel
from channel.jira import jira_channel
from config import config
from session.manager import DISCORD_SESSION_PREFIX, JIRA_SESSION_PREFIX

# Lazy import test_case_skill to avoid circular dependency
try:
    from skills.test_case_generator.skill import test_case_skill
except ImportError:
    test_case_skill = None

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


async def handle_discord_message(message: str, session_id: str, user_name: str) -> str:
    """Handle a Discord message and return response."""
    try:
        response = await agent.process(
            message=message,
            session_id=session_id,
            user_name=user_name,
        )
        return response
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        return f"Sorry, I encountered an error: {str(e)}"


async def handle_jira_message(
    message: str,
    session_id: str,
    user_name: str,
    issue_key: str,
) -> str:
    """Handle a Jira comment and return response."""
    try:
        # Check for test case generation command
        if jira_channel.is_test_case_command(message):
            return await handle_test_case_generation(issue_key, user_name)
        
        # Normal conversation
        response = await agent.process(
            message=message,
            session_id=session_id,
            user_name=user_name,
        )
        return response
    except Exception as e:
        logger.error(f"Error processing Jira comment: {e}")
        return f"Sorry, I encountered an error: {str(e)}"


async def handle_test_case_generation(issue_key: str, user_name: str) -> str:
    """Handle test case generation request for a Jira issue."""
    try:
        # Get issue requirements from description
        requirements = await jira_channel.get_issue_description(issue_key)
        
        if not requirements:
            return f"I could not retrieve the description for issue {issue_key}. Please ensure the issue has a requirements description."
        
        # Generate test cases using the skill
        logger.info(f"Generating test cases for {issue_key} based on requirements")
        
        test_cases = await test_case_skill.generate(
            requirements=requirements,
            framework="pytest",
            language="python",
            test_type="unit",
        )
        
        # Send intro comment
        intro = f"## Test Cases Generated ✅\n\nBased on the requirements description for **{issue_key}**, I have generated automated test cases for you."
        await jira_channel.add_comment_text_only(issue_key, intro)
        
        # Send code block as separate comment
        await jira_channel.add_comment_code_block(issue_key, test_cases, language="python")
        
        # Confirmation
        return f"Test cases for {issue_key} have been generated and added to the issue comments."
        
    except Exception as e:
        logger.error(f"Error generating test cases: {e}")
        return f"Error generating test cases: {str(e)}"


class Gateway:
    """Simple HTTP/WebSocket gateway for OpenClaw Mini."""

    def __init__(self):
        self.mode = config.discord.get("mode", "bot")  # 'bot' or 'webhook'
        self.jira_enabled = config.jira.get("enabled", False)
        self.host = config.server.get("host", "0.0.0.0")
        self.port = config.server.get("port", 8000)
        self.app = web.Application()
        self.runner: web.AppRunner = None
        self.site: web.TCPSite = None

        # Register routes (only for webhook mode or API endpoints)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/api/sessions", self.handle_list_sessions)
        self.app.router.add_post("/api/sessions/{session_id}/clear", self.handle_clear_session)
        self.app.router.add_post("/api/test", self.handle_test_message)
        self.app.router.add_post("/api/config/reload", self.handle_config_reload)

        # Webhook routes
        if self.mode == "webhook":
            self.app.router.add_post("/webhook/discord", self.handle_discord_webhook)
        
        if self.jira_enabled:
            self.app.router.add_post("/webhook/jira", self.handle_jira_webhook)

    async def handle_health(self, request: Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "ok", 
            "service": "openclaw-mini",
            "mode": self.mode
        })

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
                response = await handle_discord_message(content, session_id, username)

                # Send response to Discord
                await discord_channel.send_message(response, channel_id)

                logger.info(f"Processed message {message_id} from {username}")

                return web.json_response({
                    "status": "processed",
                    "message_id": message_id,
                })

            return web.json_response({"status": "ok"})

        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def handle_list_sessions(self, request: Request) -> web.Response:
        """List all active sessions."""
        from session.manager import session_manager
        sessions = session_manager.list_sessions()
        return web.json_response({"sessions": sessions, "count": len(sessions)})

    async def handle_clear_session(self, request: Request) -> web.Response:
        """Clear a session's history."""
        from session.manager import session_manager
        session_id = request.match_info.get("session_id", "")

        if session_id:
            session_manager.clear_history(session_id)
            return web.json_response({"status": "cleared", "session_id": session_id})
        else:
            return web.json_response({"status": "error", "message": "session_id required"}, status=400)

    async def handle_config_reload(self, request: Request) -> web.Response:
        """Reload configuration from config.yaml.
        
        POST /api/config/reload
        Returns: {"status": "ok", "reloaded": true|false}
        """
        from config import config
        reloaded = config.reload()
        return web.json_response({
            "status": "ok",
            "reloaded": reloaded,
            "message": "Configuration reloaded" if reloaded else "No changes detected",
        })

    async def handle_test_message(self, request: Request) -> web.Response:
        """Test endpoint for sending a message to the agent via HTTP.
        
        POST /api/test
        Body: {"message": "your message here", "session_id": "optional-session-id"}
        """
        try:
            data = await request.json()
            message = data.get("message", "")
            session_id = data.get("session_id", "test-session")
            
            if not message:
                return web.json_response({"status": "error", "message": "message required"}, status=400)
            
            # Process message through agent
            response = await agent.process(
                message=message,
                session_id=session_id,
                user_name="http-tester",
            )
            
            return web.json_response({
                "status": "ok",
                "message": message,
                "response": response,
                "session_id": session_id,
            })
            
        except Exception as e:
            logger.error(f"Test message error: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def handle_jira_webhook(self, request: Request) -> web.Response:
        """Handle Jira webhook events."""
        try:
            payload = await request.json()

            # Handle Jira webhook
            result = jira_channel.handle_webhook_payload(payload)
            
            if not result:
                # Not a comment event or filtered out
                return web.json_response({"status": "ignored", "reason": "not_comment_event"})

            issue_key = result.get("issue_key", "")
            comment_body = result.get("body", "").strip()
            username = result.get("username", "unknown")
            comment_id = result.get("comment_id", "")

            if not comment_body:
                return web.json_response({"status": "ignored", "reason": "empty_comment"})

            # Create session ID
            session_id = f"{JIRA_SESSION_PREFIX}{issue_key}"

            # Process message through agent
            response = await handle_jira_message(comment_body, session_id, username, issue_key)

            # Send response back to Jira as a comment (handles long responses)
            await jira_channel.add_comment_long(issue_key, response)

            logger.info(f"Processed Jira comment for {issue_key} from {username}")

            return web.json_response({
                "status": "processed",
                "issue_key": issue_key,
                "comment_id": comment_id,
            })

        except Exception as e:
            logger.error(f"Jira webhook error: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def start(self) -> None:
        """Start the gateway server."""
        if self.mode == "bot":
            # Bot API mode - start Discord bot and HTTP server in parallel
            # Use create_task because discord_channel.start() blocks
            bot_task = asyncio.create_task(discord_channel.start(message_callback=handle_discord_message))
            
            # Small delay to let bot start
            await asyncio.sleep(2)
            
            # Start HTTP server for API endpoints (including test endpoint)
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, self.host, self.port)
            await self.site.start()
            
            logger.info(f"Gateway started in Bot API mode on http://{self.host}:{self.port}")
        else:
            # Webhook mode - start HTTP server and Discord session
            await discord_channel.start()
            
            # Start HTTP server
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, self.host, self.port)
            await self.site.start()

            logger.info(f"Gateway started on http://{self.host}:{self.port} (Webhook mode)")

        # Start Jira channel if enabled
        if self.jira_enabled:
            await jira_channel.start_session()
            logger.info("Jira channel enabled and ready")

    async def stop(self) -> None:
        """Stop the gateway server."""
        await discord_channel.stop()
        
        if self.jira_enabled:
            await jira_channel.close_session()
        
        if self.runner:
            await self.runner.cleanup()
        logger.info("Gateway stopped")


# Global gateway instance
gateway = Gateway()
