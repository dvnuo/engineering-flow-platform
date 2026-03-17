"""Gateway server for Engineering Flow Platform."""

import asyncio
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict

from aiohttp import web
from aiohttp.web import Request

import os
import re
import hashlib

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.truncate import truncate
from src.agents.core import agent
from src.channels.jira import jira_channel
from src.config import config
from src.sessions.manager import JIRA_SESSION_PREFIX


# Lazy import webchat to avoid circular dependency
try:
    from .webchat import setup_webchat_routes
except ImportError:
    setup_webchat_routes = None

logger = logging.getLogger(__name__)


def get_traceback_str() -> str:
    """Get current exception traceback as string."""
    exc_info = sys.exc_info()
    if exc_info[0]:
        return "".join(traceback.format_exception(*exc_info))
    return "N/A"


async def handle_jira_message(
    message: str,
    session_id: str,
    user_name: str,
    issue_key: str,
) -> str:
    """Handle a Jira comment and return response."""
    try:
        logger.info(f"Processing Jira message | issue_key={issue_key} | session_id={session_id}")

        # Check for test case generation command
        if jira_channel.is_test_case_command(message):
            # Test case generation feature removed in PR #131
            # Inform user and skip
            await jira_channel.send_message(issue_key, "Test case generation feature is temporarily unavailable.")
            return ""

        # Normal conversation
        result = await agent.process(
            message=message,
            session_id=session_id,
            user_name=user_name,
        )
        response = result["response"]
        logger.info(f"Jira message processed successfully | issue_key={issue_key}")
        return response

    except asyncio.CancelledError:
        logger.info("[Memory] Periodic check cancelled")
        raise

    except Exception as e:
        tb_str = get_traceback_str()
        logger.error(f"Error processing Jira comment | issue_key={issue_key} | error={e}", exc_info=True)
        return f"Sorry, I encountered an error: {str(e)}"


class Gateway:
    """Simple HTTP/WebSocket gateway for Engineering Flow Platform."""

    def __init__(self):
        self.jira_enabled = config.jira.get("enabled", False)
        self.host = config.server.get("host", "0.0.0.0")
        self.port = config.server.get("port", 8000)
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

        # Register routes (only for webhook mode or API endpoints)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/actuator/health", self.handle_health)
        self.app.router.add_get("/api/git-info", self.handle_git_info)
        self.app.router.add_get("/api/sessions", self.handle_list_sessions)
        self.app.router.add_post("/api/sessions/{session_id}/clear", self.handle_clear_session)
        self.app.router.add_post("/api/test", self.handle_test_message)
        self.app.router.add_post("/api/config/reload", self.handle_config_reload)
        self.app.router.add_get("/api/queue/status", self.handle_queue_status)

        # Settings routes
        self.app.router.add_get("/api/settings", self.handle_settings_get)
        self.app.router.add_post("/api/settings", self.handle_settings_post)
        self.app.router.add_get("/api/settings/providers", self.handle_settings_providers)
        self.app.router.add_get("/api/settings/ollama/models", self.handle_ollama_models)
        self.app.router.add_post("/api/settings/ollama/pull", self.handle_ollama_pull)

        # Webhook routes
        if self.jira_enabled:
            self.app.router.add_post("/webhook/jira", self.handle_jira_webhook)

        # WebChat routes (if available)
        if setup_webchat_routes:
            setup_webchat_routes(self.app)

        # Setup event routes (WebSocket for real-time events)
        try:
            from .events import setup_event_routes

            setup_event_routes(self.app)
        except asyncio.CancelledError:
            logger.info("[Memory] Periodic check cancelled")
            raise
        except Exception as e:
            logger.warning(f"Could not setup event routes: {e}")
            logger.info("WebChat UI enabled at /")

    async def handle_health(self, request: Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"status": "ok", "service": "engineering-flow-platform"})

    async def handle_git_info(self, request: Request) -> web.Response:
        """Get git commit info."""
        commit_id = None
        repo_url = None

        # Check mounted code directory first (/app)
        app_root = Path("/app")
        
        # Try to get commit via git rev-parse from /app
        if (app_root / ".git").exists():
            try:
                import subprocess
                result = subprocess.run(
                    ["git", "-C", str(app_root), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    commit_id = result.stdout.strip()
                result = subprocess.run(
                    ["git", "-C", str(app_root), "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    repo_url = result.stdout.strip()
            except Exception:
                pass

        # Fallback: derive repository root from this file location
        repo_root = Path(__file__).resolve().parents[2]

        # Allow override of commit file path via environment variable
        commit_file_env = os.getenv("COMMIT_FILE_PATH")
        commit_file = Path(commit_file_env) if commit_file_env else repo_root / ".commit-id"
        repo_file_env = os.getenv("REPO_URL_FILE_PATH")
        repo_file = Path(repo_file_env) if repo_file_env else repo_root / ".repo-url"

        # Try to read commit ID from file (e.g. written by init container)
        if commit_file.exists() and not commit_id:
            try:
                with commit_file.open("r") as f:
                    commit_id = f.read().strip()
            except Exception:
                pass

        # Try to read repo URL from file (written by init container)
        if repo_file.exists():
            try:
                with repo_file.open("r") as f:
                    repo_url = f.read().strip()
            except Exception:
                pass

        # Try to get current commit via git rev-parse, if still unknown
        git_dir = repo_root / ".git"
        if commit_id is None and git_dir.exists():
            try:
                import subprocess
                result = subprocess.run(
                    ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    commit_id = result.stdout.strip()
                result = subprocess.run(
                    ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    repo_url = result.stdout.strip()
            except Exception:
                pass

        def clean_repo_url(url: str) -> str:
            """Remove username, password, and port from a git repo URL."""
            if not url: return url
            url = re.sub(r"^https?://[^@]+@", "https://", url)
            url = re.sub(r"(https?://[^/:]+):\d+", r"\1", url)
            return url  # clean repo_url repo_url = clean_repo_url(repo_url)

        # clean repo url
        repo_url = clean_repo_url(repo_url)
        return web.json_response({
            "commit_id": commit_id,
            "repo_url": repo_url,
        })

    async def handle_list_sessions(self, request: Request) -> web.Response:
        """List all active sessions with details.

        GET /api/sessions?limit=10
        Returns: List of sessions with name, last message, timestamp
        """
        from datetime import datetime
        from src.sessions.manager import session_manager

        logger.info(f"[handle_list_sessions] ENTERING - listing sessions")

        try:
            # Initialize session manager if needed
            if not session_manager._initialized:
                logger.info("[handle_list_sessions] Initializing session manager")
                await session_manager.initialize()

            # Pagination parameters
            limit = int(request.query.get("limit", 20))
            offset = int(request.query.get("offset", 0))

            session_ids = await session_manager.list_sessions()
            logger.info(f"[handle_list_sessions] Found {len(session_ids)} sessions")

            # Get all sessions with their details first
            sessions_with_details = []
            for session_id in session_ids:
                # Get session with full history (not get_session_info which excludes history)
                session = await session_manager.get_session(session_id)

                if not session:
                    logger.warning(f"[handle_list_sessions] No session: {session_id}")
                    continue

                history = session.get("history", [])

                # Skip empty sessions (no user messages)
                user_messages = [msg for msg in history if msg.get("role") == "user"]
                if not user_messages:
                    continue

                # Get first user message as session name
                first_user_msg = user_messages[0]
                session_name = truncate(first_user_msg.get("content", "") or "New Chat", 30)
                if not session_name.strip():
                    session_name = "New Chat"

                # Get last message preview
                last_message = ""
                for msg in reversed(history):
                    if msg.get("role") in ("user", "assistant"):
                        last_message = truncate(msg.get("content", "") or "", 50)
                        break

                updated_at = session.get("updated_at", datetime.utcnow().isoformat())

                sessions_with_details.append(
                    {
                        "session_id": session_id,
                        "name": session_name,
                        "last_message": last_message,
                        "updated_at": updated_at,
                        "message_count": len(user_messages),
                    }
                )

            # Sort by updated_at descending (newest first)
            sessions_with_details.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

            # Apply pagination
            total_count = len(sessions_with_details)
            detailed_sessions = sessions_with_details[offset : offset + limit]
            has_more = offset + limit < total_count

            for s in detailed_sessions:
                s["_marker"] = "FIXED_2026_02_10_17_20"
                logger.info(f"[handle_list_sessions] Added session: {s['session_id']} -> name='{s['name']}'")

            logger.info(
                f"[handle_list_sessions] Returning {len(detailed_sessions)} sessions (offset={offset}, has_more={has_more})"
            )
            return web.json_response({"sessions": detailed_sessions, "has_more": has_more, "total": total_count})

        except asyncio.CancelledError:
            logger.info("[Memory] Periodic check cancelled")
            raise
        except Exception as e:
            logger.error(f"[handle_list_sessions] ERROR: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_clear_session(self, request: Request) -> web.Response:
        """Clear a session's history."""
        from src.sessions.manager import session_manager

        session_id = request.match_info.get("session_id", "")

        if session_id:
            await session_manager.clear_history(session_id)
            return web.json_response({"status": "cleared", "session_id": session_id})
        return web.json_response({"status": "error", "message": "session_id required"}, status=400)

    async def handle_settings_get(self, request: Request) -> web.Response:
        """Get current settings.

        GET /api/settings
        Returns: {...config}
        """
        from src.config import config as runtime_config

        return web.json_response(
            {
                "llm": {
                    "provider": runtime_config.llm.get("provider"),
                    "model": runtime_config.llm.get("model"),
                    "api_base": runtime_config.llm.get("api_base"),
                    "temperature": runtime_config.llm.get("temperature"),
                    "max_tokens": runtime_config.llm.get("max_tokens"),
                },
                "jira": {
                    "enabled": bool(runtime_config.jira.get("webhook_url")),
                },
            }
        )

    async def handle_settings_post(self, request: Request) -> web.Response:
        """Update settings.

        POST /api/settings
        Body: {"llm": {...}, ...}
        Returns: {"status": "ok"}
        """
        try:
            data = await request.json()
            # For now, just validate the settings
            if "llm" in data:
                llm = data["llm"]
                if "provider" in llm and llm["provider"] not in ["openai", "github_copilot", "claude", "ollama"]:
                    return web.json_response({"status": "error", "message": "Invalid provider"}, status=400)
            return web.json_response({"status": "ok", "message": "Settings validated. Restart required to apply."})

        except asyncio.CancelledError:
            logger.info("[Memory] Periodic check cancelled")
            raise
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    async def handle_settings_providers(self, request: Request) -> web.Response:
        """Get provider information.

        GET /api/settings/providers
        Returns: {provider: {name, default_model, models: [...]}}
        """
        from src.agents.llm import llm_client

        return web.json_response(llm_client.get_provider_info())

    async def handle_ollama_models(self, request: Request) -> web.Response:
        """Get Ollama models.

        GET /api/settings/ollama/models
        Returns: {"status": "healthy", "models": [...]}
        """
        from src.agents.llm import llm_client

        return web.json_response(await llm_client.check_provider_health("ollama"))

    async def handle_ollama_pull(self, request: Request) -> web.Response:
        """Pull an Ollama model.

        POST /api/settings/ollama/pull
        Body: {"model": "llama3"}
        """
        try:
            data = await request.json()
            model = data.get("model")
            if not model:
                return web.json_response({"status": "error", "message": "model required"}, status=400)

            from src.agents.llm import llm_client

            if "ollama" not in llm_client.providers:
                return web.json_response({"status": "error", "message": "Ollama not configured"}, status=400)

            result = await llm_client.providers["ollama"].pull_model(model)
            return web.json_response({"status": "success", "result": result})

        except asyncio.CancelledError:
            logger.info("[Memory] Periodic check cancelled")
            raise
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def handle_config_reload(self, request: Request) -> web.Response:
        """Reload configuration from config.yaml.

        POST /api/config/reload
        Returns: {"status": "ok", "reloaded": true|false}
        """
        from src.config import config as runtime_config

        reloaded = runtime_config.reload()
        return web.json_response(
            {
                "status": "ok",
                "reloaded": reloaded,
                "message": "Configuration reloaded" if reloaded else "No changes detected",
            }
        )

    async def handle_queue_status(self, request: Request) -> web.Response:
        """Get execution queue status.

        GET /api/queue/status
        Returns: {"status": "ok", "queues": {...}, "active_sessions": N}
        """
        try:
            from src.agents.queue import execution_queue

            queues = await execution_queue.list_all_queues()
            return web.json_response(
                {
                    "status": "ok",
                    "queues": queues,
                    "active_sessions": execution_queue.get_active_sessions(),
                }
            )

        except asyncio.CancelledError:
            logger.info("[Memory] Periodic check cancelled")
            raise
        except Exception as e:
            logger.error(f"Queue status error: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def handle_test_message(self, request: Request) -> web.Response:
        """Test endpoint for sending a message to the agent via HTTP.

        POST /api/test
        Body: {"message": "your message here", "session_id": "optional-session-id", "reasoning_replay": false}
        """
        try:
            data = await request.json()
            message = data.get("message", "")
            session_id = data.get("session_id", "test-session")
            reasoning_replay = data.get("reasoning_replay", None)

            if not message:
                return web.json_response({"status": "error", "message": "message required"}, status=400)

            # Process message through agent
            result = await agent.process(
                message=message,
                session_id=session_id,
                user_name="http-tester",
                reasoning_replay=reasoning_replay,
            )

            if result is None:
                return web.json_response({"status": "error", "message": "Agent returned None"}, status=500)

            response_data = {
                "status": "ok",
                "message": message,
                "response": result.get("response", ""),
                "session_id": session_id,
            }

            # Include reasoning if available
            if "reasoning" in result:
                response_data["reasoning"] = result["reasoning"]

            # Include usage if available
            if "usage" in result:
                response_data["usage"] = result["usage"]

            return web.json_response(response_data)

        except asyncio.CancelledError:
            logger.info("[Memory] Periodic check cancelled")
            raise
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Test message error: {e}\nTraceback:\n{tb}")
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

            return web.json_response({"status": "processed", "issue_key": issue_key, "comment_id": comment_id})

        except asyncio.CancelledError:
            logger.info("[Memory] Periodic check cancelled")
            raise
        except Exception as e:
            tb_str = get_traceback_str()
            logger.error(f"Jira webhook error | error={e} | traceback={truncate(tb_str, 200)}", exc_info=True)
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def start(self) -> None:
        """Start the gateway server."""
        # Start HTTP server
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        logger.info(f"Gateway started on http://{self.host}:{self.port}")

        # Run memory bootstrap in background after server starts
        asyncio.create_task(self._run_memory_bootstrap())

        # Jira channel is initialized in __init__ with HTTP client ready
        if self.jira_enabled and jira_channel.is_configured():
            logger.info("Jira channel enabled and ready")

    async def _run_memory_bootstrap(self) -> None:
        """Run memory bootstrap in background."""
        try:
            from src.memory.daily_generator import ensure_daily_memories
            from src.memory.long_term_generator import update_long_term_memory_from_daily
            from src.config import config as runtime_config

            logger.info("[Memory] Starting background bootstrap...")

            workspace = runtime_config.session.get("workspace", "/root/.efp/workspace")

            # Create daily memories (without LLM for now)
            created_daily = await ensure_daily_memories(
                workspace=workspace,
                llm_client=None,
                backfill_only_missing=True,
            )

            logger.info(f"[Memory] Bootstrap complete: {len(created_daily) if created_daily else 0} daily files")

            # Generate long-term memory from recent dailies
            try:
                from src.agents.llm import llm_client as runtime_llm_client
                if runtime_llm_client and created_daily:
                    logger.info("[Memory] Generating long-term memory from daily files...")
                    await update_long_term_memory_from_daily(
                        workspace=workspace,
                        llm_client=runtime_llm_client,
                        daily_paths=created_daily,
                    )
                    logger.info("[Memory] Long-term memory updated")
            except Exception as e:
                logger.warning(f"[Memory] Long-term memory update skipped: {e}")


            # Start periodic check for session changes
            await self._start_periodic_memory_check(workspace)

        except asyncio.CancelledError:
            logger.info("[Memory] Periodic check cancelled")
            raise
        except Exception as e:
            logger.error(f"[Memory] Bootstrap failed: {e}")

    async def _start_periodic_memory_check(self, workspace: str):
        """Periodically check for session changes and update daily memory."""
        from pathlib import Path
        from src.memory.daily_generator import ensure_daily_memories

        CHECK_INTERVAL = 3600  # 1 hour in seconds
        sessions_dir = Path(workspace) / ".sessions"

        last_mtime = 0

        # Get initial file modification times
        if sessions_dir.exists():
            for f in sessions_dir.glob("*.jsonl"):
                last_mtime = max(last_mtime, f.stat().st_mtime)

        logger.info("[Memory] Starting periodic session check...")

        while True:
            await asyncio.sleep(CHECK_INTERVAL)

            try:
                # Check if any session file has been modified
                current_mtime = 0
                if sessions_dir.exists():
                    for f in sessions_dir.glob("*.jsonl"):
                        current_mtime = max(current_mtime, f.stat().st_mtime)

                # If new activity, regenerate today's memory
                if current_mtime > last_mtime:
                    logger.info("[Memory] Session changes detected, updating daily memory...")
                    created_daily = await ensure_daily_memories(
                        workspace=workspace,
                        llm_client=None,
                        backfill_only_missing=True,  # Always regenerate today
                    )
                    logger.info(f"[Memory] Updated: {len(created_daily) if created_daily else 0} daily files")

                    # Also update long-term memory
                    try:
                        from src.agents.llm import llm_client as runtime_llm_client
                        if runtime_llm_client:
                            logger.info("[Memory] Updating long-term memory...")
                            await update_long_term_memory_from_daily(
                                workspace=workspace,
                                llm_client=runtime_llm_client,
                                daily_paths=created_daily,
                            )
                            logger.info("[Memory] Long-term memory updated")
                    except Exception as e:
                        logger.warning(f"[Memory] Long-term update skipped: {e}")

                    last_mtime = current_mtime
                else:
                    logger.debug("[Memory] No session changes detected")

            except asyncio.CancelledError:
                logger.info("[Memory] Periodic check cancelled")
                raise
            except Exception as e:
                logger.error(f"[Memory] Periodic check failed: {e}")

    async def stop(self) -> None:
        """Stop the gateway server."""
        # Close Jira client if it was initialized
        if self.jira_enabled:
            try:
                await jira_channel.close()
                logger.info("Jira channel closed")
            except asyncio.CancelledError:
                logger.info("[Memory] Periodic check cancelled")
                raise
            except Exception as e:
                logger.warning(f"Error closing Jira channel: {e}")

        if self.runner:
            await self.runner.cleanup()
        logger.info("Gateway stopped")


# Global gateway instance
gateway = Gateway()
