"""Gateway server for Engineering Flow Platform."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import traceback
import uuid
from pathlib import Path

from aiohttp import web
from aiohttp.web import Request

import os
import re

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.truncate import truncate
from src.config import config
from src.workspace_defaults import resolve_runtime_workspace
from src.external_cli import jira as jira_cli
from src.gateway.runtime_chat import run_runtime_chat
from src.efp_runtime.session.gateway_facade import (
    JIRA_SESSION_PREFIX,
    runtime_session_manager as session_manager,
)


# Lazy import runtime API route registration to avoid circular dependency
try:
    from .runtime_api import setup_runtime_api_routes
except ImportError:
    setup_runtime_api_routes = None

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_AGENTS_SECTION = "agents"
SYSTEM_PROMPT_AGENTS_FILENAME = "AGENTS.md"

# Access logging. Request ids are reused from the caller when supplied so a
# Portal-side trace and a runtime-side trace line up in kubectl logs.
REQUEST_ID_HEADERS = ("X-Request-Id", "X-Trace-Id")
# aiohttp's own access log is disabled: ``access_log_middleware`` already emits
# a richer http.start/http.end pair carrying the request id (which aiohttp's
# ``%{X-Request-Id}i`` cannot see, because it reads the *inbound* header and so
# prints "-" whenever the middleware mints the id — the common case). Leaving
# both on emitted two near-duplicate lines per request.
ACCESS_LOG = None
# Status reported for a client disconnect / cancelled handler (nginx idiom).
CLIENT_CLOSED_REQUEST_STATUS = 499

# Upload sizing. EFP_MAX_UPLOAD_MB is the user-facing per-file cap the Portal
# enforces and reports; the runtime allows that plus headroom for multipart /
# transport overhead so it is never the gate for a file the Portal accepted.
DEFAULT_MAX_UPLOAD_MB = 25
UPLOAD_TRANSPORT_HEADROOM_MB = 5


def resolve_upload_client_max_size() -> int:
    """aiohttp ``client_max_size`` (bytes) for request bodies incl. uploads."""
    raw = os.getenv("EFP_MAX_UPLOAD_MB", str(DEFAULT_MAX_UPLOAD_MB))
    try:
        mb = int(str(raw).strip())
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_UPLOAD_MB
    if mb <= 0:
        mb = DEFAULT_MAX_UPLOAD_MB
    return (mb + UPLOAD_TRANSPORT_HEADROOM_MB) * 1024 * 1024


def _sanitize_request_id(value: object, max_len: int = 64) -> str:
    """One-line, greppable request id (mirrors runtime_api trace sanitizing)."""
    if value is None:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9._:-]+", "", str(value)).strip()
    return cleaned[:max_len]


def resolve_request_id(request: Request) -> str:
    """Reuse an inbound correlation id when present, else mint a short one."""
    headers = getattr(request, "headers", {}) or {}
    for header in REQUEST_ID_HEADERS:
        candidate = _sanitize_request_id(headers.get(header))
        if candidate:
            return candidate
    return uuid.uuid4().hex[:12]


@web.middleware
async def access_log_middleware(request: Request, handler):
    """Log one ``http.start`` line before and one ``http.end`` line after.

    The start line is emitted before awaiting the handler on purpose: a slow
    handler (e.g. a synchronous workspace snapshot) otherwise leaves no
    evidence in stdout that the request was even received.
    """
    request_id = resolve_request_id(request)
    request["request_id"] = request_id
    method = request.method
    path = request.path
    remote = request.remote or "-"
    started = time.perf_counter()
    status = 500

    logger.info(
        f"http.start method={method} path={path} remote={remote} request_id={request_id}"
    )
    try:
        response = await handler(request)
        status = getattr(response, "status", status)
        return response
    except web.HTTPException as http_error:
        status = http_error.status
        raise
    except asyncio.CancelledError:
        status = CLIENT_CLOSED_REQUEST_STATUS
        raise
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        logger.info(
            f"http.end method={method} path={path} status={status} "
            f"duration_ms={duration_ms:.1f} request_id={request_id}"
        )


def _runtime_workspace_root() -> Path:
    """Canonical runtime workspace root."""
    try:
        config_data = config.get_effective_config()
    except Exception:
        config_data = getattr(config, "_config", None)
    return resolve_runtime_workspace(config_data).resolve()


def _agents_md_path() -> Path:
    return _runtime_workspace_root() / SYSTEM_PROMPT_AGENTS_FILENAME


def _default_agents_md_template_path() -> Path:
    return Path(__file__).resolve().parents[2] / "workspace" / "AGENTS.md.example"


def _read_default_agents_md_template() -> str:
    path = _default_agents_md_template_path()
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return "# AGENTS.md\n\n"


def _write_agents_md(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)
    return path


def _ensure_agents_md() -> Path:
    path = _agents_md_path()
    if path.exists():
        return path
    return _write_agents_md(path, _read_default_agents_md_template())


def _read_agents_md() -> str:
    return _ensure_agents_md().read_text(encoding="utf-8")


def _agents_metadata() -> dict[str, object]:
    return {
        "enabled": True,
        "editable": True,
        "label": SYSTEM_PROMPT_AGENTS_FILENAME,
        "filename": SYSTEM_PROMPT_AGENTS_FILENAME,
        "path": SYSTEM_PROMPT_AGENTS_FILENAME,
        "can_disable": False,
    }


def _agents_system_prompt_config_payload() -> dict[str, object]:
    _ensure_agents_md()
    return {
        "engine": "native",
        "runtime_type": "native",
        "sections": [SYSTEM_PROMPT_AGENTS_SECTION],
        SYSTEM_PROMPT_AGENTS_SECTION: _agents_metadata(),
    }


def _unsupported_system_prompt_response(name: str) -> web.Response:
    return web.json_response(
        {
            "error": "EFP Native runtime only supports AGENTS.md",
            "engine": "native",
            "runtime_type": "native",
            "section": name,
            "supported_sections": [SYSTEM_PROMPT_AGENTS_SECTION],
        },
        status=422,
    )



def _clean_repo_url(url: str) -> str:
    """Remove username, password, and port from a git repo URL."""
    if not url:
        return url
    url = re.sub(r"^https?://[^@]+@", "https://", url)
    url = re.sub(r"(https?://[^/:]+):\d+", r"\1", url)
    return url


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

        result = await run_runtime_chat(
            message=message,
            session_id=session_id,
            user_name=user_name,
            request_path="jira",
            execution_metadata={"issue_key": issue_key, "source": "jira"},
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


def _extract_jira_comment_event(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
    comment = payload.get("comment") if isinstance(payload.get("comment"), dict) else {}
    issue_key = str(issue.get("key") or payload.get("issue_key") or "").strip()
    body = str(comment.get("body") or payload.get("body") or "").strip()
    if not issue_key or not body:
        return None
    author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
    username = (
        str(author.get("displayName") or author.get("name") or author.get("accountId") or "").strip()
        or str(payload.get("username") or "").strip()
        or "unknown"
    )
    return {
        "issue_key": issue_key,
        "body": body,
        "username": username,
        "comment_id": str(comment.get("id") or payload.get("comment_id") or "").strip(),
    }


class Gateway:
    """Simple HTTP/WebSocket gateway for Engineering Flow Platform."""

    def __init__(self):
        self.jira_enabled = config.jira.get("enabled", False)
        self.host = config.server.get("host", "0.0.0.0")
        self.port = config.server.get("port", 8000)
        self.app = web.Application(
            client_max_size=resolve_upload_client_max_size(),
            middlewares=[access_log_middleware],
        )
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

        # Register routes (only for webhook mode or API endpoints)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/actuator/health", self.handle_health)
        self.app.router.add_get("/ready", self.handle_ready)
        self.app.router.add_get("/api/git-info", self.handle_git_info)
        self.app.router.add_get("/api/skill-git-info", self.handle_skill_git_info)
        self.app.router.add_post("/api/sessions/{session_id}/clear", self.handle_clear_session)
        self.app.router.add_get("/api/queue/status", self.handle_queue_status)

        # System prompt config routes
        self.app.router.add_get("/api/agent/system-prompt/config", self.handle_system_prompt_config_get)
        self.app.router.add_put("/api/agent/system-prompt/config", self.handle_system_prompt_config_put)
        self.app.router.add_get("/api/agent/system-prompt/{name}", self.handle_system_prompt_get)
        self.app.router.add_put("/api/agent/system-prompt/{name}", self.handle_system_prompt_put)

        # Webhook routes
        if self.jira_enabled:
            self.app.router.add_post("/webhook/jira", self.handle_jira_webhook)

        # Runtime API routes (if available)
        if setup_runtime_api_routes:
            setup_runtime_api_routes(self.app)

        # Setup event routes (WebSocket for real-time events)
        try:
            from .events import setup_event_routes

            setup_event_routes(self.app)
        except asyncio.CancelledError:
            logger.info("[Memory] Periodic check cancelled")
            raise
        except Exception as e:
            logger.warning(f"Could not setup event routes: {e}")
            logger.info("Runtime event routes are unavailable")

    async def handle_health(self, request: Request) -> web.Response:
        """Health check endpoint (liveness; always ok)."""
        return web.json_response({"status": "ok", "service": "engineering-flow-platform"})

    async def handle_ready(self, request: Request) -> web.Response:
        """Readiness endpoint gated on the boot-time profile projection.

        Returns 200 only after bootstrap_profile_boot() completed successfully;
        503 otherwise so the pod stays unready when the profile is broken.
        """
        from src.config import config as runtime_config, get_profile_boot_state

        boot_state = get_profile_boot_state()
        external_status = runtime_config.get_external_config_status()
        if boot_state.get("completed") and boot_state.get("ready") and external_status.get("success"):
            meta = runtime_config.get_managed_overlay_meta()
            return web.json_response(
                {
                    "ready": True,
                    "runtime_profile_id": meta.get("runtime_profile_id"),
                    "revision": meta.get("revision"),
                }
            )
        error = (
            boot_state.get("error")
            or external_status.get("error")
            or "runtime profile boot projection has not completed"
        )
        return web.json_response({"ready": False, "error": error}, status=503)

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

        repo_url = _clean_repo_url(repo_url)
        return web.json_response({
            "commit_id": commit_id,
            "repo_url": repo_url,
        })


    async def handle_skill_git_info(self, request: Request) -> web.Response:
        """Get git commit info for the mounted skills repository."""
        commit_id = None
        repo_url = None

        skills_dir_env = os.getenv("EFP_SKILLS_DIR")
        skills_dir = Path(skills_dir_env).expanduser() if skills_dir_env and skills_dir_env.strip() else Path("/app/skills")

        if skills_dir.exists() and (skills_dir / ".git").exists():
            try:
                import subprocess

                result = subprocess.run(
                    ["git", "-C", str(skills_dir), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    commit_id = result.stdout.strip()

                result = subprocess.run(
                    ["git", "-C", str(skills_dir), "remote", "get-url", "origin"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    repo_url = result.stdout.strip()
            except Exception:
                pass

        return web.json_response({"commit_id": commit_id, "repo_url": _clean_repo_url(repo_url)})

    async def handle_clear_session(self, request: Request) -> web.Response:
        """Clear a session's history."""
        session_id = request.match_info.get("session_id", "")

        if session_id:
            await session_manager.clear_history(session_id)
            return web.json_response({"status": "cleared", "session_id": session_id})
        return web.json_response({"status": "error", "message": "session_id required"}, status=400)

    # ============================================
    # System Prompt Configuration Handlers
    # ============================================
    
    async def handle_system_prompt_config_get(self, request: Request) -> web.Response:
        """Get the runtime-owned system prompt configuration."""

        return web.json_response(_agents_system_prompt_config_payload())
    
    async def handle_system_prompt_config_put(self, request: Request) -> web.Response:
        """Validate system prompt configuration updates.

        Native runtime only supports AGENTS.md, which is always enabled.
        """

        try:
            data = await request.json()
        except asyncio.CancelledError:
            raise
        except Exception:
            return web.json_response({"error": "Invalid payload", "engine": "native"}, status=400)

        if not isinstance(data, dict):
            return web.json_response({"error": "Invalid payload", "engine": "native"}, status=400)
        for section in data:
            if section != SYSTEM_PROMPT_AGENTS_SECTION:
                return _unsupported_system_prompt_response(section)
        section_payload = data.get(SYSTEM_PROMPT_AGENTS_SECTION, {})
        if not isinstance(section_payload, dict):
            return web.json_response({"error": "Invalid section payload", "engine": "native"}, status=400)
        if "enabled" in section_payload and not isinstance(section_payload["enabled"], bool):
            return web.json_response({"error": "Invalid enabled", "engine": "native"}, status=400)
        if section_payload.get("enabled") is False:
            return web.json_response(
                {
                    "error": "EFP Native AGENTS.md cannot be disabled",
                    "engine": "native",
                    "runtime_type": "native",
                    "section": SYSTEM_PROMPT_AGENTS_SECTION,
                    "supported_sections": [SYSTEM_PROMPT_AGENTS_SECTION],
                },
                status=422,
            )
        _ensure_agents_md()
        return web.json_response(
            {
                "status": "ok",
                "engine": "native",
                "runtime_type": "native",
                "sections": [SYSTEM_PROMPT_AGENTS_SECTION],
                SYSTEM_PROMPT_AGENTS_SECTION: _agents_metadata(),
            }
        )
    
    async def handle_system_prompt_get(self, request: Request) -> web.Response:
        """Get AGENTS.md content and enabled state."""

        name = request.match_info.get("name")
        if name != SYSTEM_PROMPT_AGENTS_SECTION:
            return _unsupported_system_prompt_response(str(name or ""))
        content = _read_agents_md()
        return web.json_response({
            "enabled": True,
            "content": content,
            "engine": "native",
            "runtime_type": "native",
            "section": SYSTEM_PROMPT_AGENTS_SECTION,
            **_agents_metadata(),
        })
    
    async def handle_system_prompt_put(self, request: Request) -> web.Response:
        """Update AGENTS.md content.

        The AGENTS.md section is always enabled and cannot be toggled off.
        """

        name = request.match_info.get("name")
        if name != SYSTEM_PROMPT_AGENTS_SECTION:
            return _unsupported_system_prompt_response(str(name or ""))

        try:
            data = await request.json()
        except asyncio.CancelledError:
            raise
        except Exception:
            return web.json_response({"error": "Invalid payload", "engine": "native"}, status=400)

        if not isinstance(data, dict):
            return web.json_response({"error": "Invalid payload", "engine": "native"}, status=400)
        if "enabled" in data and not isinstance(data["enabled"], bool):
            return web.json_response({"error": "Invalid enabled", "engine": "native"}, status=400)
        if "content" in data and not isinstance(data["content"], str):
            return web.json_response({"error": "Invalid content", "engine": "native"}, status=400)
        if data.get("enabled") is False:
            return web.json_response(
                {
                    "error": "EFP Native AGENTS.md cannot be disabled",
                    "engine": "native",
                    "runtime_type": "native",
                    "section": SYSTEM_PROMPT_AGENTS_SECTION,
                    "supported_sections": [SYSTEM_PROMPT_AGENTS_SECTION],
                },
                status=422,
            )
        if "content" in data:
            _write_agents_md(_agents_md_path(), data["content"])
        else:
            _ensure_agents_md()

        return web.json_response(
            {
                "status": "ok",
                "engine": "native",
                "runtime_type": "native",
                "section": SYSTEM_PROMPT_AGENTS_SECTION,
                "enabled": True,
                "path": SYSTEM_PROMPT_AGENTS_FILENAME,
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

    async def handle_jira_webhook(self, request: Request) -> web.Response:
        """Handle Jira webhook events."""
        try:
            payload = await request.json()

            result = _extract_jira_comment_event(payload)

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
            await jira_cli.add_comment_long(issue_key, response)

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
        self.runner = web.AppRunner(self.app, access_log=ACCESS_LOG)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        logger.info(f"Gateway started on http://{self.host}:{self.port}")

        if self.jira_enabled:
            logger.info("Jira webhook route enabled; writeback uses external jira CLI")

    async def stop(self) -> None:
        """Stop the gateway server."""
        if self.runner:
            await self.runner.cleanup()
        logger.info("Gateway stopped")


# Global gateway instance
gateway = Gateway()
