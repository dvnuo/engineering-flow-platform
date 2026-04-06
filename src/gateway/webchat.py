"""WebChat UI and HTTP server for Engineering Flow Platform.

A simple web interface to chat with the agent directly.

UNIQUE_MARKER_12345
"""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from aiohttp import web, ContentTypeError
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.file_parser.storage import init_storage, _file_metadata, StoredFileNotFoundError, get_metadata
init_storage()
from src.utils.file_parser import parse_file
from src.utils.truncate import truncate
from src.utils.redaction import safe_preview, safe_log_field, sanitize_exception_message


from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

# Module-level YAML instance for reuse
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 4096

from src.agents.core import Agent as AgentCore
from src.agents.core import run_chat_execution
from src.hooks.session_memory import save_session_summary
from src.agents.errors import extract_error_details, LLMError
from src.hooks.file_context import inject_context
from src.config import config as global_config
from src.runtime.chat_orchestration_adapter import execute_chat_orchestration
from src.runtime import build_default_execution_bus, make_execution_request
from src.sessions.manager import session_manager
from src.sessions.persistence import session_persistence
from src.sessions.usage import usage_tracker

logger = logging.getLogger(__name__)


# Get template and static paths
TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
MAX_PORTAL_IDENTITY_LENGTH = 256


def _sanitize_portal_identity_value(value: Any) -> str:
    raw = "" if value is None else str(value)
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", raw).strip()
    return cleaned[:MAX_PORTAL_IDENTITY_LENGTH]


def _extract_portal_identity(request: web.Request, data: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    headers = getattr(request, "headers", {}) or {}
    header_user_id = _sanitize_portal_identity_value(headers.get("X-Portal-User-Id"))
    header_user_name = _sanitize_portal_identity_value(headers.get("X-Portal-User-Name"))
    body_user_id = _sanitize_portal_identity_value(data.get("portal_user_id"))
    body_user_name = _sanitize_portal_identity_value(data.get("portal_user_name"))

    resolved_user_id = header_user_id or body_user_id or None
    resolved_user_name = header_user_name or body_user_name or None

    if header_user_id or header_user_name:
        source = "headers"
    elif body_user_id or body_user_name:
        source = "body"
    else:
        source = "none"
    logger.debug("[portal_identity] resolved_source=%s has_user_id=%s has_user_name=%s", source, bool(resolved_user_id), bool(resolved_user_name))
    return resolved_user_id, resolved_user_name


def _require_non_empty_string(data: Dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required and must be a non-empty string")
    return value.strip()


def _optional_string(data: Dict[str, Any], key: str) -> Optional[str]:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    stripped = value.strip()
    return stripped or None


def _parse_task_execute_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse and validate /api/tasks/execute request payload."""
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")

    task_id = _require_non_empty_string(data, "task_id")
    task_type = _require_non_empty_string(data, "task_type")

    input_payload = data.get("input_payload")
    if not isinstance(input_payload, dict):
        raise ValueError("input_payload is required and must be a JSON object")

    metadata = data.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")

    return {
        "task_id": task_id,
        "task_type": task_type,
        "input_payload": dict(input_payload),
        "session_id": _optional_string(data, "session_id"),
        "source": _optional_string(data, "source"),
        "workflow_rule_id": _optional_string(data, "workflow_rule_id"),
        "shared_context_ref": _optional_string(data, "shared_context_ref"),
        "metadata": dict(metadata),
    }


def _json_compatible(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


async def _run_chat_via_execution_bus(
    *,
    agent: AgentCore,
    session_id: str,
    message: str,
    user_name: str,
    portal_user_id: Optional[str],
    portal_user_name: Optional[str],
    attached_images: Optional[List[str]] = None,
    attachments: Optional[List[str]] = None,
    reasoning_replay: Optional[bool] = None,
    stream_callback: Optional[Any] = None,
    request_path: str = "/api/chat",
) -> Dict[str, Any]:
    async def _chat_handler(execution_request):
        payload = execution_request.input_payload
        return await run_chat_execution(
            agent,
            message=payload.get("message", ""),
            session_id=execution_request.session_id or session_id,
            user_name=payload.get("user_name"),
            portal_user_id=payload.get("portal_user_id"),
            portal_user_name=payload.get("portal_user_name"),
            attached_images=payload.get("attached_images"),
            attachments=payload.get("attachments"),
            track_usage=bool(payload.get("track_usage", True)),
            reasoning_replay=payload.get("reasoning_replay"),
            stream_callback=payload.get("stream_callback"),
        )

    execution_result = await execute_chat_orchestration(
        request_id=f"chat-{uuid.uuid4()}",
        session_id=session_id,
        source_ref="webchat",
        input_payload={
            "message": message,
            "user_name": user_name,
            "portal_user_id": portal_user_id,
            "portal_user_name": portal_user_name,
            "attached_images": attached_images,
            "attachments": attachments,
            "track_usage": True,
            "reasoning_replay": reasoning_replay,
            "stream_callback": stream_callback,
        },
        metadata={"path": request_path, "persist_last_execution_id": True},
        chat_handler=_chat_handler,
    )
    output_payload = execution_result.output_payload if isinstance(execution_result.output_payload, dict) else {}
    if execution_result.status == "error" or output_payload.get("error"):
        error_value = output_payload.get("error", "Execution bus error")
        if isinstance(error_value, dict):
            error_message = (
                error_value.get("message")
                or error_value.get("error")
                or json.dumps(error_value, ensure_ascii=False)
            )
        else:
            error_message = str(error_value)
        raise web.HTTPInternalServerError(
            text=json.dumps({"error": error_message}, ensure_ascii=False),
            content_type="application/json",
        )
    return output_payload


def _resolve_runtime_agent_identity(request: web.Request) -> tuple[Optional[str], Optional[str]]:
    """Resolve runtime agent identity from server-side state/config, never from client body."""
    runtime_agent_id: Optional[str] = None
    runtime_agent_name: Optional[str] = None

    app = request.app

    try:
        global_config.reload()
    except Exception:
        pass
    config_data = getattr(global_config, "_config", {}) or {}

    def _cfg(path: str) -> str:
        value = config_data
        for part in path.split("."):
            if not isinstance(value, dict):
                return ""
            value = value.get(part)
            if value is None:
                return ""
        return str(value).strip()

    raw_agent_id = app.get("agent_id") if hasattr(app, "get") else None
    raw_agent_name = app.get("agent_name") if hasattr(app, "get") else None
    if raw_agent_id:
        runtime_agent_id = str(raw_agent_id).strip() or None
    if raw_agent_name:
        runtime_agent_name = str(raw_agent_name).strip() or None

    raw_agent = app.get("agent") if hasattr(app, "get") else None
    if raw_agent:
        if isinstance(raw_agent, dict):
            if not runtime_agent_id:
                runtime_agent_id = str(raw_agent.get("id") or raw_agent.get("agent_id") or "").strip() or None
            if not runtime_agent_name:
                runtime_agent_name = str(raw_agent.get("name") or raw_agent.get("agent_name") or raw_agent.get("display_name") or "").strip() or None
        else:
            if not runtime_agent_id:
                runtime_agent_id = str(getattr(raw_agent, "id", None) or getattr(raw_agent, "agent_id", None) or "").strip() or None
            if not runtime_agent_name:
                runtime_agent_name = str(getattr(raw_agent, "name", None) or getattr(raw_agent, "agent_name", None) or getattr(raw_agent, "display_name", None) or "").strip() or None

    if not runtime_agent_name:
        runtime_agent_name = (
            _cfg("agent.name")
            or _cfg("agent.display_name")
            or _cfg("server.name")
            or None
        )

    if not runtime_agent_id:
        runtime_agent_id = (
            _cfg("agent.id")
            or _cfg("server.id")
            or None
        )

    if not runtime_agent_name:
        runtime_agent_name = "Assistant"

    return runtime_agent_id, runtime_agent_name


def load_template(filename: str) -> str:
    """Load HTML template from file."""
    template_path = TEMPLATE_DIR / filename
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


async def serve_webchat(request: web.Request) -> web.Response:
    """Serve the WebChat UI."""
    try:
        html_content = load_template("webchat.html")
        return web.Response(
            text=html_content,
            content_type='text/html',
            headers={
                'Cache-Control': 'no-cache, no-store, must-revalidate',
            }
        )
    except FileNotFoundError:
        logger.error(f"WebChat template not found: {TEMPLATE_DIR / 'webchat.html'}")
        return web.Response(
            text="<html><body><h1>WebChat template not found</h1></body></html>",
            status=500,
            content_type='text/html'
        )


async def serve_static(request: web.Request) -> web.Response:
    """Serve static files (CSS, JS)."""
    path = request.match_info.get('path', '')
    file_path = STATIC_DIR / path
    
    # Security: prevent directory traversal
    try:
        file_path = file_path.resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())):
            return web.Response(status=403, text="Forbidden")
    except (ValueError, OSError):
        return web.Response(status=400, text="Invalid path")
    
    if not file_path.exists():
        return web.Response(status=404, text="Not found")
    
    # Determine content type
    content_type = 'text/plain'
    if file_path.suffix == '.css':
        content_type = 'text/css'
    elif file_path.suffix == '.js':
        content_type = 'application/javascript'
    elif file_path.suffix == '.html':
        content_type = 'text/html'
    elif file_path.suffix == '.json':
        content_type = 'application/json'
    elif file_path.suffix == '.png':
        content_type = 'image/png'
    elif file_path.suffix == '.jpg' or file_path.suffix == '.jpeg':
        content_type = 'image/jpeg'
    elif file_path.suffix == '.svg':
        content_type = 'image/svg+xml'
    elif file_path.suffix == '.ico':
        content_type = 'image/x-icon'
    
    try:
        with open(file_path, 'rb') as f:
            content = f.read()
        return web.Response(
            body=content,
            content_type=content_type,
            headers={
                'Cache-Control': 'public, max-age=3600',
            }
        )
    except Exception as e:
        logger.error(f"Error serving static file {file_path}: {e}")
        return web.Response(status=500, text="Internal server error")


async def api_chat(request: web.Request) -> web.Response:
    """Handle chat API requests.
    
    POST /api/chat
    Body: {"message": "...", "session_id": "optional", "attachments": ["file_id1", "file_id2"], "reasoning_replay": false}
    """
    try:
        data = await request.json()
        message = (data.get('message') or '').strip()
        
        # Dynamic session_id with timestamp-based default for multi-session support
        session_id = data.get('session_id', f'webchat_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}')
        
        # Get reasoning_replay setting
        reasoning_replay = data.get('reasoning_replay', None)
        
        # Get attachments from new field
        attachments = data.get('attachments', [])
        portal_user_id, portal_user_name = _extract_portal_identity(request, data)
        effective_user_name = portal_user_name or "webchat-user"
        logger.debug(
            "[api_chat] Request summary: session_id=%s, has_message=%s, attachment_count=%d, portal_user_id_present=%s",
            session_id,
            bool(message),
            len(attachments) if isinstance(attachments, list) else 0,
            bool(portal_user_id),
        )
        
        if not message and not attachments:
            return web.json_response({'error': 'Empty message'}, status=400)
        
        # Check if LLM is configured before processing
        api_key = global_config.llm.get('api_key')
        if not api_key:
            return web.json_response({
                'error': 'LLM not configured',
                'message': 'Please configure LLM API Key in Settings to use the chat feature.',
                'code': 'llm_not_configured'
            }, status=503)
        
        logger.info(f"[api_chat] Processing message for session: {session_id}")
        
        # Process attachments from both @file_ references (backward compat) and attachments field
        attached_images = []
        
        # Helper to process a single file_id
        async def process_file(file_id: str) -> bool:
            """Process a file_id and add to attached_images if valid. Returns True if processed."""
            nonlocal attached_images
            # Only process first image to avoid large payloads
            if attached_images:
                return True
            try:
                from src.utils.file_parser.storage import get_metadata, get_file_path, StoredFileNotFoundError
                metadata = get_metadata(file_id)
                # Validate session ownership
                if metadata.session_id and metadata.session_id != session_id:
                    logger.warning(f"[api_chat] File {file_id} belongs to different session")
                    return False
                # Check if it's an image
                if metadata.content_type and metadata.content_type.startswith('image/'):
                    file_path = get_file_path(file_id)
                    if file_path.exists():
                        import base64
                        img_data = await asyncio.to_thread(
                            lambda: base64.b64encode(file_path.read_bytes()).decode('utf-8')
                        )
                        ext = metadata.content_type.split('/')[-1]
                        attached_images.append(f'data:image/{ext};base64,{img_data}')
                        return True
            except StoredFileNotFoundError:
                logger.warning(f"[api_chat] File {file_id} not found")
            except Exception as e:
                logger.warning(f"[api_chat] Failed to process file {safe_preview(file_id, 80)}: {sanitize_exception_message(e)}")
            return False
        
        # 1. Parse @file_ references from message (backward compatibility)
        try:
            refs = re.findall(r'@file_([a-zA-Z0-9]+)', message)
            for short_id in set(refs):
                # Try exact match first, then prefix match
                try:
                    from src.utils.file_parser.storage import get_metadata
                    metadata = get_metadata(short_id)
                    if await process_file(metadata.file_id):
                        break  # Stop after first image
                except (StoredFileNotFoundError, ValueError):
                    # Try prefix match using public helper (handles short/ambiguous prefixes)
                    from src.utils.file_parser.storage import find_file_by_prefix
                    try:
                        fid = find_file_by_prefix(short_id)
                        if fid:
                            await process_file(fid)
                    except ValueError as ve:
                        logger.warning(f"[api_chat] Prefix lookup failed: {sanitize_exception_message(ve)}")
        except Exception as e:
            logger.warning(f"[api_chat] @file_ parse error: {sanitize_exception_message(e)}")
        
        # 2. Process attachments from new attachments field
        if attachments and isinstance(attachments, list):
            for file_id in attachments:
                await process_file(file_id)
        
        # Set placeholder message if only images
        if attached_images and not message.strip():
            message = "[image]"
        
        # Always ensure input field is present for Copilot API downstream
        if not message:
            logger.error(f"[api_chat] ERROR: Final message is empty before Copilot API call. Payload: {json.dumps(data, ensure_ascii=False)}")
            return web.json_response({'error': 'Input field missing for Copilot API.'}, status=400)

        # Revalidate: if no message and no attached images, return error
        if not message.strip() and not attached_images:
            return web.json_response({'error': 'Empty message'}, status=400)

        # Inject file context if user has uploaded files
        original_msg_for_history = message if message.strip() else ("[image]" if attached_images else "")
        logger.info("[api_chat] Message summary: session_id=%s attached_images=%d message_length=%d preview=%s", safe_log_field(session_id, 120), len(attached_images) if attached_images else 0, len(original_msg_for_history), safe_preview(original_msg_for_history, 120))
        original_message = message
        try:
            enhanced_message, budget_status, citations = inject_context(
                session_id=session_id,
                message=message,
                top_k=5,
                max_tokens=4000
            )
            if enhanced_message and enhanced_message != message:
                logger.info(f"[api_chat] File context injected: status={budget_status}, chunks={len(citations)}")
                message = enhanced_message
                # Attach citations to request for response
                request['file_citations'] = citations
        except Exception as e:
            logger.warning(f"[api_chat] File context injection failed: {sanitize_exception_message(e)}")
            # Continue without file context if injection fails
        # Revalidate message is not empty to prevent downstream LLM input from being empty
        if not message or not message.strip():
            logger.error(f"[api_chat] ERROR: Final message is empty before Copilot API call. Payload: {json.dumps(data, ensure_ascii=False)}")
            return web.json_response({'error': 'Input field missing for Copilot API.'}, status=400)
        
        # Initialize session manager if needed
        if not session_manager._initialized:
            await session_manager.initialize()
        
        # All requests go through LLM - LLM decides when to use tools based on user input
        # Tools are registered via src/__init__.py and available to LLM via tool_calls
        # This is the Claude Code style - no separate skill matching/execution needed
        
        # Get model from config
        model = global_config.llm.get('model', 'gpt-5-mini')
        
        # Run agent (history is managed internally by session_manager)
        runtime_agent_id, runtime_agent_name = _resolve_runtime_agent_identity(request)
        agent = AgentCore(
            model=model,
            session_id=session_id,
            agent_id=runtime_agent_id,
            agent_name=runtime_agent_name,
        )
        result = await _run_chat_via_execution_bus(
            agent=agent,
            message=message,
            session_id=session_id,
            user_name=effective_user_name,
            portal_user_id=portal_user_id,
            portal_user_name=portal_user_name,
            reasoning_replay=reasoning_replay,
            attached_images=attached_images if attached_images else None,
            attachments=attachments if attachments else None,
            request_path="/api/chat",
        )
        
        # Force save session to persistence
        session = await session_manager.get_session(session_id)
        logger.info(f"[api_chat] Session after agent.process(): {session is not None}")
        if session and session.get("history"):
            logger.info(f"[api_chat] Saving session with {len(session['history'])} messages")
            await session_persistence.save_session(
                session_id=session_id,
                channel=session.get("channel", ""),
                messages=session["history"],
                metadata=session.get("metadata", {}),
            )
        else:
            logger.warning(f"[api_chat] No session or empty history for {session_id}")
        
        response = result.get("response", "") if result else ""
        usage = result.get("usage", {}) if result else {}
        reasoning = result.get("reasoning", "") if result else ""
        
        # Record usage if available
        if usage:
            provider = global_config.llm.get('provider', 'openai')
            model = global_config.llm.get('model', 'gpt-5-mini')
            usage_tracker.record_usage(
                provider=provider,
                model=model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                session_id=session_id,
                task_type="chat"
            )
        
        response_data = {
            'response': response,
            'session_id': session_id,
            'usage': usage
        }
        
        # Include user_message_id for frontend to update optimistic UI
        if result and isinstance(result, dict):
            user_msg_id = result.get("user_message_id")
            if user_msg_id:
                response_data['user_message_id'] = user_msg_id
        
        # Include events for thinking process display
        events = result.get("events", []) if result else []
        if events:
            response_data['events'] = events
            # Save events to session for persistence
            if 'metadata' not in session:
                session['metadata'] = {}
            session['metadata']['thinking_events'] = events
            logger.info(f"[api_chat] Saved {len(events)} thinking events to session metadata")
        
        # Include LLM debug info for sidebar display
        llm_debug = result.get("_llm_debug", {}) if result else {}
        if llm_debug:
            response_data['_llm_debug'] = llm_debug
        
        # Always save thinking events to chatlog (even without llm_debug)
        chatlog_dir = os.path.join(session_persistence.storage_dir, "chatlogs")
        os.makedirs(chatlog_dir, exist_ok=True)
        chatlog_file = os.path.join(chatlog_dir, f"{session_id}.json")
        try:
            chatlog_data = {
                "session_id": session_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "metadata": session.get('metadata', {}),
                "events": events,
            }
            if llm_debug:
                chatlog_data["llm_debug"] = llm_debug
            # Add skill mode info if present
            if session.get('metadata', {}).get('active_skill_session'):
                chatlog_data["skill_session"] = session.get('metadata', {}).get('active_skill_session')
            with open(chatlog_file, "w") as f:
                json.dump(chatlog_data, f, indent=2)
            logger.info(f"[api_chat] Saved chatlog with {len(events)} events to {chatlog_file}")
        except Exception as e:
            logger.warning(f"[api_chat] Failed to save chatlog: {e}")
        
        # Include reasoning if available
        if reasoning:
            response_data['reasoning'] = reasoning
        
        return web.json_response(response_data)
        
    except json.JSONDecodeError:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    except web.HTTPException:
        raise
    except Exception as e:
        # Get detailed error information
        error_details = extract_error_details(e)
        
        # Log full error details
        logger.error(f"Chat error: {sanitize_exception_message(e)}")
        logger.error(f"Error details: {safe_preview(error_details, 800)}")
        
        # Return user-friendly error message with optional details
        user_message = str(e)
        error_type = error_details.get("error_type", "unknown")
        status_code = 500
        
        # Map error types to HTTP status codes
        if error_type == "bad_request":
            status_code = 400
        elif error_type == "authentication_error":
            status_code = 401
        elif error_type == "rate_limit":
            status_code = 429
        elif error_type == "server_error":
            status_code = 500
        
        # Try to get a user-friendly message
        if isinstance(e, LLMError):
            # Use the error's message
            user_message = e.message
            status_code = e.status_code or status_code
        
        return web.json_response({
            'error': user_message,
            'error_type': error_type,
            'details': error_details.get("details", {}),
            'timestamp': error_details.get("timestamp"),
        }, status=status_code)


async def api_chat_stream(request: web.Request) -> web.StreamResponse:
    """Handle streaming chat API requests (Server-Sent Events).
    
    POST /api/chat/stream
    Body: {"message": "...", "session_id": "optional"}
    
    Returns: text/event-stream with chunks of the response
    """
    try:
        data = await request.json()
        message = (data.get('message') or '').strip()
        session_id = data.get('session_id', f'webchat_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}')
        portal_user_id, portal_user_name = _extract_portal_identity(request, data)
        effective_user_name = portal_user_name or "webchat-user"
        
        if not message:
            response = web.json_response({'error': 'Empty message'}, status=400)
            return response
        
        # Create streaming response
        response = web.StreamResponse(
            status=200,
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',  # Disable nginx buffering
            }
        )
        
        await response.prepare(request)
        
        # Send start event
        await response.write(f"event: start\ndata: {json.dumps({'session_id': session_id})}\n\n".encode())
        
        # Create an async queue for streaming events
        import asyncio
        event_queue = asyncio.Queue()
        
        # Get model from config
        model = global_config.llm.get('model', 'gpt-5-mini')
        
        # Run agent and stream response
        runtime_agent_id, runtime_agent_name = _resolve_runtime_agent_identity(request)
        agent = AgentCore(
            model=model,
            session_id=session_id,
            agent_id=runtime_agent_id,
            agent_name=runtime_agent_name,
        )
        
        # Pass the queue to the agent for real-time events
        result = await _run_chat_via_execution_bus(
            agent=agent,
            message=message,
            session_id=session_id,
            user_name=effective_user_name,
            portal_user_id=portal_user_id,
            portal_user_name=portal_user_name,
            stream_callback=event_queue,
            request_path="/api/chat/stream",
        )
        
        # Stream events from queue while agent is running
        while not event_queue.empty():
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                escaped = event.replace('\n', '\\n').replace('\r', '\\r')
                await response.write(f"event: progress\ndata: {escaped}\n\n".encode())
            except asyncio.TimeoutError:
                break
            except Exception as e:
                logger.error(f"Error streaming event: {e}")
        
        response_text = result.get("response", "") if result else ""
        usage = result.get("usage", {}) if result else {}
        
        # Record usage
        if usage:
            provider = global_config.llm.get('provider', 'openai')
            model = global_config.llm.get('model', 'gpt-5-mini')
            usage_tracker.record_usage(
                provider=provider,
                model=model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                session_id=session_id,
                task_type="chat"
            )
        
        # Send usage data
        usage_data = json.dumps({
            'usage': usage,
            'session_id': session_id,
        })
        await response.write(f"event: usage\ndata: {usage_data}\n\n".encode())
        
        # Send done event
        await response.write(f"event: done\ndata: \n\n".encode())
        
        return response
        
    except json.JSONDecodeError:
        response = web.json_response({'error': 'Invalid JSON'}, status=400)
        return response
    except web.HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stream error: {e}")
        error_data = json.dumps({'error': str(e)})
        try:
            await response.write(f"event: error\ndata: {error_data}\n\n".encode())
        except Exception:
            pass
        return web.Response(status=500, text=str(e))


async def api_tasks_execute(request: web.Request) -> web.Response:
    """Handle runtime task execution requests.

    POST /api/tasks/execute
    """
    try:
        data = await request.json()
        parsed = _parse_task_execute_request(data)

        merged_input_payload = dict(parsed["input_payload"])
        merged_input_payload["task_type"] = parsed["task_type"]

        metadata = dict(parsed["metadata"])
        metadata["task_id"] = parsed["task_id"]
        metadata["portal_task_id"] = parsed["task_id"]
        metadata["path"] = "/api/tasks/execute"
        if parsed["source"]:
            metadata["portal_task_source"] = parsed["source"]
        if parsed["workflow_rule_id"]:
            metadata["portal_workflow_rule_id"] = parsed["workflow_rule_id"]
        if parsed["shared_context_ref"]:
            metadata["shared_context_ref"] = parsed["shared_context_ref"]

        bus = build_default_execution_bus()
        execution_request = make_execution_request(
            request_id=f"task-{parsed['task_id']}",
            source_type="task",
            source_ref=parsed["source"] or "portal",
            execution_type="task",
            session_id=parsed["session_id"],
            input_payload=merged_input_payload,
            metadata=metadata,
        )
        execution_result = await bus.execute(execution_request)

        status = execution_result.status
        is_ok = status == "success"
        output_payload = execution_result.output_payload

        response_payload: Dict[str, Any] = {
            "ok": is_ok,
            "task_id": parsed["task_id"],
            "execution_type": "task",
            "request_id": execution_result.request_id,
            "status": status,
            "output_payload": _json_compatible(output_payload),
            "artifacts": _json_compatible(execution_result.artifacts),
            "runtime_events": _json_compatible(execution_result.runtime_events),
            "next_action_hint": execution_result.next_action_hint,
            "audit_ref": execution_result.audit_ref,
        }
        if status in {"error", "blocked"}:
            if isinstance(output_payload, dict):
                response_payload["error"] = output_payload.get("error") or output_payload
            else:
                response_payload["error"] = str(output_payload)

        return web.json_response(response_payload)
    except (json.JSONDecodeError, ContentTypeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.error("Task execution API error: %s", sanitize_exception_message(exc), exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def api_sessions(request: web.Request) -> web.Response:
    """List recent sessions with details.
    
    GET /api/sessions?limit=10
    Returns: List of sessions with name, last message, timestamp
    
    VERSION: FINAL_TEST_2026_02_10_17_10
    """
    import time
    start_time = time.time()
    logger.info(f"[api_sessions FINAL_TEST_2026_02_10_17_10] ENTERING - checking version")
    logger.info(f"[FINAL_TEST] Source file: /root/engineering-flow-platform/src/gateway/webchat.py")
    try:
        # Initialize session manager if needed
        if not session_manager._initialized:
            logger.info("[api_sessions] Initializing session manager")
            await session_manager.initialize()
        
        limit = int(request.query.get('limit', 10))
        session_ids = await session_manager.list_sessions()
        logger.info(f"[api_sessions] Found {len(session_ids)} sessions: {session_ids[:5]}")
        
        # Format sessions with details, filter out empty sessions
        detailed_sessions = []
        for session_id in session_ids[:limit]:
            # Get session info
            session_info = await session_manager.get_session_info(session_id)
            
            if not session_info:
                logger.warning(f"[api_sessions] No info for session: {session_id}")
                continue
            
            history = session_info.get('history', [])
            
            # Skip empty sessions (no user messages)
            user_messages = [msg for msg in history if msg.get('role') == 'user']
            if not user_messages:
                logger.info(f"[api_sessions] Skipping empty session: {session_id}")
                continue
            
            # Get first user message as session name
            first_user_msg = user_messages[0]
            session_name = truncate(first_user_msg.get('content', '') or 'New Chat', 30)
            if not session_name.strip():
                session_name = 'New Chat'
            
            # Get last message preview
            last_message = ''
            for msg in reversed(history):
                if msg.get('role') in ('user', 'assistant'):
                    last_message = truncate(msg.get('content', '') or '', 50)
                    break
            
            detailed_sessions.append({
                'session_id': session_id,
                'name': session_name,
                'last_message': last_message,
                'updated_at': session_info.get('updated_at', datetime.utcnow().isoformat()),
                'message_count': len(user_messages),
                '_marker': 'FIXED_2026_02_10_16_58',  # Version marker
            })
            logger.info(f"[api_sessions] Added session: {session_id} -> name='{session_name}'")
        
        logger.info(f"[api_sessions] Returning {len(detailed_sessions)} sessions")
        return web.json_response({'sessions': detailed_sessions})
    except Exception as e:
        logger.error(f"[api_sessions] ERROR: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_load_session(request: web.Request) -> web.Response:
    """Load session messages.
    
    GET /api/sessions/{session_id}
    Returns: Session messages
    """
    try:
        # Initialize session manager if needed
        if not session_manager._initialized:
            await session_manager.initialize()
        
        session_id = request.match_info.get('session_id', '')
        if not session_id:
            return web.json_response({'error': 'Session ID required'}, status=400)
        
        session_info = await session_manager.get_session(session_id)
        
        if not session_info:
            return web.json_response({'error': 'Session not found'}, status=404)
        
        history = session_info.get('history', [])
        
        # Extract session name from first user message
        session_name = 'New Chat'
        for msg in history:
            if msg.get('role') == 'user':
                content = msg.get('content', '') or 'New Chat'
                session_name = truncate(content, 30)
                break
        
        return web.json_response({
            'session_id': session_id,
            'name': session_name,
            'messages': history,
            'metadata': session_info.get('metadata', {}),
        })
    except Exception as e:
        logger.error(f"Error loading session: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_session_chatlog(request: web.Request) -> web.Response:
    """Load session LLM chatlog.
    
    GET /api/sessions/{session_id}/chatlog
    Returns: LLM request/response data
    """
    try:
        session_id = request.match_info.get('session_id', '')
        if not session_id:
            return web.json_response({'error': 'Session ID required'}, status=400)
        
        # Load from chatlog file
        chatlog_file = os.path.join(session_persistence.storage_dir, "chatlogs", f"{session_id}.json")
        
        if os.path.exists(chatlog_file):
            with open(chatlog_file, "r") as f:
                chatlog_data = json.load(f)
            return web.json_response(chatlog_data)
        else:
            # Return empty object for new sessions instead of 404
            # This prevents 404 error in Thinking Process panel
            return web.json_response({'session_id': session_id, 'messages': [], 'metadata': {}})
            
    except Exception as e:
        logger.error(f"Error loading chatlog: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_browse_files(request: web.Request) -> web.Response:
    """Browse file directory.
    
    GET /api/files?path=/workspace
    Returns: List of files and directories
    """
    try:
        # Default to /root for file browser
        path = request.query.get('path', '/root')
        base_path = Path(path)
        
        if not base_path.exists():
            return web.json_response({'error': 'Path not found', 'path': path}, status=404)
        
        items = []
        for item in sorted(base_path.iterdir()):
            items.append({
                'name': item.name,
                'path': str(item.resolve()),
                'is_dir': item.is_dir(),
                'is_file': item.is_file(),
            })
        
        return web.json_response({'path': str(base_path.resolve()), 'items': items})
    except Exception as e:
        logger.error(f"Error browsing files: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_read_file(request: web.Request) -> web.Response:
    """Read file content.
    
    GET /api/files/read?path=/path/to/file
    Returns: File content and metadata
    """
    try:
        path = request.query.get('path', '')
        if not path:
            return web.json_response({'error': 'Path required'}, status=400)
        
        file_path = Path(path)
        
        if not file_path.exists():
            return web.json_response({'error': 'File not found', 'path': path}, status=404)
        
        if not file_path.is_file():
            return web.json_response({'error': 'Not a file', 'path': path}, status=400)
        
        # Read file content
        content = file_path.read_text(encoding='utf-8')
        
        # Determine language for syntax highlighting
        ext = file_path.suffix.lower()
        language_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.html': 'html',
            '.css': 'css',
            '.json': 'json',
            '.md': 'markdown',
            '.yaml': 'yaml',
            '.yml': 'yaml',
            '.sh': 'bash',
            '.sql': 'sql',
            '.xml': 'xml',
            '.csv': 'csv',
        }
        language = language_map.get(ext, 'text')
        
        return web.json_response({
            'path': str(file_path.resolve()),
            'name': file_path.name,
            'size': file_path.stat().st_size,
            'content': content,
            'language': language,
        })
    except UnicodeDecodeError:
        # Binary file
        return web.json_response({
            'error': 'Cannot read binary file',
            'path': path,
        }, status=400)
    except Exception as e:
        logger.error(f"Error reading file: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_usage(request: web.Request) -> web.Response:
    """Get usage statistics."""
    try:
        session_id = request.query.get('session_id')
        days = int(request.query.get('days', 30))
        hours = days * 24
        
        if session_id:
            summary = usage_tracker.get_session_summary(session_id)
            return web.json_response(summary)
        else:
            global_summary = usage_tracker.get_global_summary(hours=hours)
            by_model = usage_tracker.get_usage_by_model(hours=hours)
            by_provider = usage_tracker.get_usage_by_provider(hours=hours)
            return web.json_response({
                'period_days': days,
                'global': global_summary,
                'by_model': by_model,
                'by_provider': by_provider,
            })
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


async def api_clear(request: web.Request) -> web.Response:
    """Clear chat history.
    
    POST /api/clear
    Body: {"session_id": "optional"}
    """
    try:
        data = await request.json()
        session_id = data.get('session_id', 'webchat')
        
        await session_manager.clear_history(session_id)
        
        return web.json_response({'success': True})
            
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


async def api_edit_message(request: web.Request) -> web.Response:
    """Edit a message, delete subsequent messages.
    
    After editing, the frontend should reload the session to get the updated
    state. A new LLM response will be generated when the user sends a message.
    
    POST /api/sessions/{session_id}/messages/{message_id}/edit
    Body: {"new_content": "edited message content"}
    """
    try:
        session_id = request.match_info.get('session_id')
        message_id = request.match_info.get('message_id')
        
        if not session_id or not message_id:
            return web.json_response({'error': 'Missing session_id or message_id'}, status=400)
        
        # Initialize session_manager if needed
        if not session_manager._initialized:
            await session_manager.initialize()
        
        try:
            data = await request.json()
        except (json.JSONDecodeError, ContentTypeError):
            return web.json_response({'error': 'Invalid JSON in request body'}, status=400)
        
        new_content = data.get('new_content')
        if new_content is None:
            return web.json_response({'error': "Missing 'new_content' in request body"}, status=400)
        if not isinstance(new_content, str):
            return web.json_response({'error': "'new_content' must be a string"}, status=400)
        
        # Edit the message
        edited = await session_manager.edit_message(session_id, message_id, new_content)
        if not edited:
            return web.json_response({'error': 'Message not found', 'user_message_id': message_id}, status=404)
        
        # Delete all messages after the edited message
        deleted_count = await session_manager.delete_messages_after(session_id, message_id)
        
        # Get updated history
        history = await session_manager.get_history(session_id)
        
        return web.json_response({
            'success': True,
            'deleted_count': deleted_count,
            'messages': history
        })
            
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)


async def api_delete_conversation_from(request: web.Request) -> web.Response:
    """Delete a message and all subsequent messages in the conversation.
    
    This endpoint truncates the conversation starting from the specified message.
    Frontends can use it for workflows where "editing" a message is implemented
    as delete-and-resend (delete the original and then send a new message with
    the edited content). For in-place edits of an existing message, prefer
    ``api_edit_message``.
    
    POST /api/sessions/{session_id}/messages/{message_id}/delete-from-here
    """
    try:
        session_id = request.match_info.get('session_id')
        message_id = request.match_info.get('message_id')
        
        if not session_id or not message_id:
            return web.json_response({'error': 'Missing session_id or message_id', 'user_message_id': message_id}, status=400)
        
        # Delete this message and all messages after it
        # First get the message index, then delete from there
        history = await session_manager.get_history(session_id)
        
        # Find the message index
        msg_index = None
        for i, msg in enumerate(history):
            if msg.get('id') == message_id:
                msg_index = i
                break
        
        if msg_index is None:
            return web.json_response({'error': 'Message not found', 'user_message_id': message_id}, status=404)
        
        # Delete messages from msg_index onwards
        deleted_count = 0
        if msg_index < len(history):
            # Get IDs of messages to delete, skipping any without an 'id'
            ids_to_delete = [msg.get('id') for msg in history[msg_index:] if msg.get('id')]
            for mid in ids_to_delete:
                success = await session_manager.delete_message(session_id, mid)
                if success:
                    deleted_count += 1
        
        return web.json_response({
            'success': True,
            'deleted_count': deleted_count
        })
            
    except Exception as e:
        return web.json_response({'error': str(e), 'user_message_id': message_id}, status=500)


async def api_save_config(request: web.Request) -> web.Response:
    """Save configuration to config.yaml.
    
    POST /api/config/save
    Body: JSON with config sections to update (partial updates supported)
    
    This endpoint performs partial saves - only modified fields are updated,
    preserving existing values for unchanged fields within each section.
    """
    try:
        data = await request.json()
        
        # Try project config first, then fallback to ~/.efp/
        project_config = Path(__file__).parent.parent.parent / 'config.yaml'
        project_example = Path(__file__).parent.parent.parent / 'config.yaml.example'
        efp_config = Path.home() / '.efp' / 'config.yaml'
        
        # Determine config path
        if project_config.exists():
            config_path = project_config
        elif efp_config.exists():
            config_path = efp_config
        else:
            # Create new config from example
            efp_config.parent.mkdir(parents=True, exist_ok=True)
            if project_example.exists():
                import shutil
                shutil.copy(project_example, efp_config)
                config_path = efp_config
            else:
                config_path = efp_config
            config = {}
        
        # Use module-level YAML instance (reused for performance)
        yaml = _yaml
        
        # Read existing config with ruamel.yaml (preserves comments)
        existing_config = CommentedMap()
        try:
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    existing_config = yaml.load(f) or CommentedMap()
        except Exception as e:
            logger.warning(f"Could not parse existing config, starting fresh: {e}")
            existing_config = CommentedMap()
        
        # Deep merge function - only updates provided fields, preserves others
        def deep_merge(base: Dict, update: Dict) -> Dict:
            """Deep merge update into base, preserving unchanged fields."""
            result = base.copy()
            for key, value in update.items():
                if key in result and isinstance(result.get(key), (dict, CommentedMap)) and isinstance(value, dict):
                    result[key] = deep_merge(result[key], value)
                else:
                    result[key] = value
            return result
        
        # Perform partial update - only merge provided sections
        config = existing_config.copy()
        sections = ['llm', 'jira', 'confluence', 'github', 'git', 'ssh', 'debug', 'proxy']
        
        for section in sections:
            if section in data:
                # Deep merge to preserve other fields in this section
                if section in config and isinstance(global_config.get(section), (dict, CommentedMap)):
                    config[section] = deep_merge(config[section], data[section])
                else:
                    config[section] = data[section]
        
        # Encrypt sensitive fields before saving
        global_config._encrypt_sensitive_fields(config)
        
        # Write back with preserved formatting and comments
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f)
        
        # Determine which sections changed and reload services
        updated_sections = [s for s in sections if s in data]
        if not global_config.config_path.exists():
            global_config.config_path = config_path
        global_config.reload(changed_sections=updated_sections)
        
        # Apply proxy settings if proxy section was updated
        if 'proxy' in updated_sections:
            global_config.apply_proxy()
        
        return web.json_response({
            'success': True, 
            'message': 'Configuration saved and reloaded.',
            'updated_sections': updated_sections
        })
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_get_config(request: web.Request) -> web.Response:
    """Get current configuration.
    
    GET /api/config
    """
    try:
        # Try project config first, then fallback to ~/.efp/
        project_config = Path(__file__).parent.parent.parent / 'config.yaml'
        efp_config = Path.home() / '.efp' / 'config.yaml'
        
        if project_config.exists():
            config_path = project_config
        elif efp_config.exists():
            config_path = efp_config
        else:
            return web.json_response({'error': 'config.yaml not found (checked: project dir and ~/.efp/)'}, status=404)
        
        # Use module-level YAML instance
        yaml = _yaml
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.load(f) or {}
        except Exception as e:
            logger.error(f"YAML parse error: {e}")
            return web.json_response({'error': f'YAML parse error: {e}'}, status=500)
        
        # Convert CommentedMap to regular dict for JSON response
        if hasattr(config, 'to_dict'):
            config = global_config.to_dict()
        
        # Decrypt sensitive fields before returning
        try:
            def decrypt_value(val):
                if isinstance(val, str) and val.startswith("ENC:"):
                    # Use the global config object's decrypt method
                    return global_config._decrypt_value(val)
                return val
            
            def decrypt_config(obj):
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if key in {"api_key", "password", "token", "api_token", "secret"}:
                            obj[key] = decrypt_value(value)
                        elif isinstance(value, dict):
                            decrypt_config(value)
                        elif isinstance(value, list):
                            for item in value:
                                if isinstance(item, dict):
                                    decrypt_config(item)
                elif isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, dict):
                            decrypt_config(item)
            
            decrypt_config(config)
        except Exception as e:
            logger.warning(f"Failed to decrypt config values: {e}")
        
        return web.json_response({'config': config})
    except Exception as e:
        logger.error(f"Error reading config: {e}")
        return web.json_response({'error': str(e)}, status=500)


# ========== SSH Key Management ==========

async def api_ssh_generate(request: web.Request) -> web.Response:
    """Generate SSH key pair.
    
    POST /api/ssh/generate
    Body: {"key_type": "ed25519" | "rsa", "comment": "optional comment"}
    
    Returns:
        - success: boolean
        - public_key: the public key to add to GitHub/GitLab
        - key_type: type of key generated
    """
    try:
        data = await request.json() if request.can_read_body else {}
        key_type = data.get("key_type", "rsa")
        comment = data.get("comment", "engineering-flow-platform")
        
        if key_type not in ["ed25519", "rsa"]:
            return web.json_response(
                {"error": "Invalid key_type. Use 'ed25519' or 'rsa'"},
                status=400
            )
        
        from src.git.api import generate_ssh_key
        result = await generate_ssh_key(key_type=key_type, comment=comment)
        
        if result.get("success"):
            return web.json_response({
                "success": True,
                "message": result.get("message"),
                "public_key": result.get("public_key"),
                "key_type": result.get("key_type"),
                "instructions": "Add the public key to your GitHub/GitLab account settings"
            })
        else:
            return web.json_response({
                "success": False,
                "error": result.get("error", "Failed to generate SSH key")
            }, status=500)
            
    except Exception as e:
        logger.error(f"Error generating SSH key: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def api_ssh_public_key(request: web.Request) -> web.Response:
    """Get existing SSH public key.
    
    GET /api/ssh/public-key
    
    Returns:
        - success: boolean
        - public_key: the public key (if exists)
        - key_type: type of key
    """
    try:
        from src.git.api import get_ssh_public_key
        result = await get_ssh_public_key()
        
        if result.get("success"):
            return web.json_response({
                "success": True,
                "public_key": result.get("public_key"),
                "key_type": result.get("key_type")
            })
        else:
            return web.json_response({
                "success": False,
                "message": result.get("message", "No SSH key found")
            }, status=404)
            
    except Exception as e:
        logger.error(f"Error getting SSH public key: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ========== GitHub Copilot Authorization ==========

import httpx
import uuid

# In-memory storage for pending authorizations (in production, use Redis/database)
_pending_authorizations: Dict[str, Dict[str, Any]] = {}


async def api_copilot_auth_start(request: web.Request) -> web.Response:
    """Start GitHub Copilot device authorization flow.
    
    POST /api/copilot/auth/start
    
    Returns:
        - verification_url: URL for user to authorize
        - user_code: Code to display to user
        - device_code: Device code for polling
        - expires_in: Seconds until expiration
        - interval: Polling interval in seconds
    """
    try:
        # Get GitHub base URL from config
        github_base_url = config.get("github.base_url", "https://github.com").replace("https://github.com", "").strip("/")
        api_base_url = config.get("github.api_base", "https://api.github.com")
        
        async with httpx.AsyncClient() as client:
            # Request device authorization from GitHub
            response = await client.post(
                f"{api_base_url}/copilot/token_verification",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "action": "create"
                }
            )
            
            if response.status_code != 201:
                logger.error(f"GitHub Copilot auth failed: {response.status_code} {response.text}")
                return web.json_response({
                    'error': 'Failed to start authorization',
                    'details': f'GitHub API returned {response.status_code}'
                }, status=500)
            
            data = response.json()
            
            # Store pending authorization
            device_code = data.get("device_code", str(uuid.uuid4()))
            auth_id = str(uuid.uuid4())[:8]
            
            _pending_authorizations[auth_id] = {
                'device_code': device_code,
                'user_code': data.get("user_code", ""),
                'verification_uri': data.get("verification_uri", ""),
                'verification_uri_complete': data.get("verification_uri_complete", ""),
                'expires_at': datetime.utcnow().timestamp() + data.get("expires_in", 600),
                'interval': data.get("interval", 5),
                'status': 'pending',
                'token': None,
                'created_at': datetime.utcnow().isoformat(),
            }
            
            logger.info(f"GitHub Copilot auth started: {auth_id}")
            
            return web.json_response({
                'auth_id': auth_id,
                'user_code': data.get("user_code", ""),
                'verification_url': data.get("verification_uri", ""),
                'verification_complete_url': data.get("verification_uri_complete", ""),
                'expires_in': data.get("expires_in", 600),
                'interval': data.get("interval", 5),
            })
            
    except Exception as e:
        logger.error(f"Error starting Copilot auth: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_copilot_auth_check(request: web.Request) -> web.Response:
    """Check GitHub Copilot authorization status.
    
    POST /api/copilot/auth/check
    Body: { "auth_id": "...", "device_code": "..." }
    
    Returns:
        - status: "pending" | "authorized" | "expired" | "failed"
        - token: (only if authorized) the GitHub Copilot token
    """
    try:
        import uuid
        
        data = await request.json()
        auth_id = data.get("auth_id", "")
        device_code = data.get("device_code", "")
        
        if not auth_id or not device_code:
            return web.json_response({'error': 'auth_id and device_code required'}, status=400)
        
        # Check if authorization exists
        auth = _pending_authorizations.get(auth_id)
        if not auth:
            return web.json_response({'error': 'Authorization not found or expired'}, status=404)
        
        # Check if expired
        if datetime.utcnow().timestamp() > auth['expires_at']:
            _pending_authorizations.pop(auth_id, None)
            return web.json_response({'status': 'expired', 'message': 'Authorization expired'})
        
        # Check current status
        if auth['status'] == 'authorized':
            token = auth['token']
            _pending_authorizations.pop(auth_id, None)
            return web.json_response({
                'status': 'authorized',
                'token': token,
            })
        
        # Get GitHub API base
        api_base_url = config.get("github.api_base", "https://api.github.com")
        
        async with httpx.AsyncClient() as client:
            # Check token status
            response = await client.post(
                f"{api_base_url}/copilot/token_verification",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "action": "verify",
                    "device_code": device_code
                }
            )
            
            if response.status_code == 200:
                # Authorization complete!
                token_data = response.json()
                token = token_data.get("token", "")
                
                # Update authorization status
                auth['status'] = 'authorized'
                auth['token'] = token
                
                logger.info(f"GitHub Copilot authorized: {auth_id}")
                
                # Clean up and return
                _pending_authorizations.pop(auth_id, None)
                
                return web.json_response({
                    'status': 'authorized',
                    'token': token,
                })
            elif response.status_code == 400:
                error_data = response.json()
                error = error_data.get("error", "")
                
                if error == "authorization_pending":
                    return web.json_response({'status': 'pending'})
                elif error == "expired_token":
                    _pending_authorizations.pop(auth_id, None)
                    return web.json_response({'status': 'expired', 'message': 'Device code expired'})
                elif error == "authorization_declined":
                    _pending_authorizations.pop(auth_id, None)
                    return web.json_response({'status': 'declined', 'message': 'User declined authorization'})
                else:
                    return web.json_response({'status': 'failed', 'message': error})
            else:
                return web.json_response({
                    'status': 'pending',
                    'message': 'Still waiting...'
                })
                
    except Exception as e:
        logger.error(f"Error checking Copilot auth: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


def _parse_skill_from_file(skill_path: Path) -> Optional[Dict[str, Any]]:
    """Parse a skill from SKILL.md file.
    
    Args:
        skill_path: Path to SKILL.md file
        
    Returns:
        Skill dict or None if parsing fails
    """
    try:
        content = skill_path.read_text(encoding='utf-8')
        
        # Extract skill name from first line (without # prefix)
        lines = content.strip().split('\n')
        name = ""
        description = ""
        emoji = "🔧"
        examples = []
        in_examples = False
        
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith('# ') and not name:
                name = line[2:].strip().replace(' Skill', '').lower()
            elif line.startswith('## Examples'):
                in_examples = True
                continue
            elif in_examples:
                if line.startswith('```') or line.startswith('## '):
                    in_examples = False
                    continue
                # Extract example commands from comments
                if line.startswith('# '):
                    example = line[2:].strip()
                    if example and len(example) < 80:  # Limit example length
                        examples.append(example)
            elif not description and line and not line.startswith('#'):
                description = line
        
        # Try to find emoji in first line or after #
        emoji_match = content.strip().split('\n')[0]
        if '📌' in emoji_match:
            emoji = "📌"
        elif '🔧' in emoji_match:
            emoji = "🔧"
        elif '💻' in emoji_match:
            emoji = "💻"
        elif '📝' in emoji_match:
            emoji = "📝"
        elif '🔍' in emoji_match:
            emoji = "🔍"
        elif '🌤️' in emoji_match:
            emoji = "🌤️"
        
        return {
            "name": name,
            "description": description,
            "emoji": emoji,
            "path": str(skill_path.parent.name),
            "examples": examples[:3],  # Limit to 3 examples
        }
    except Exception:
        return None


def _get_skills_list() -> List[Dict[str, Any]]:
    """Get list of all available skills.
    
    Returns:
        List of skill dictionaries
    """
    skills = []
    project_root = Path(__file__).resolve().parent.parent.parent
    
    # Check multiple locations for skills
    skill_dirs = [
        project_root / "skills",
        project_root / "src" / "skills",
    ]
    
    for skill_dir in skill_dirs:
        if not skill_dir.exists():
            continue
            
        for skill_path in skill_dir.iterdir():
            if skill_path.is_dir():
                skill_file = skill_path / "SKILL.md"
                if skill_file.exists():
                    skill = _parse_skill_from_file(skill_file)
                    if skill and skill["name"]:
                        skills.append(skill)
    
    return skills


async def api_skills(request: web.Request) -> web.Response:
    """Get list of available skills.
    
    GET /api/skills
    Returns: List of skills with name, description, triggers
    """
    try:
        from src.skills import skill_registry
        
        # Load skills if not already loaded
        if not skill_registry._initialized:
            skill_registry.load_skills()
        
        # Return new skill registry format
        skills = skill_registry.get_all_skill_summaries()
        
        return web.json_response({'skills': skills})
    except Exception as e:
        logger.error(f"Error getting skills: {e}")
        return web.json_response({'error': str(e), 'skills': []}, status=500)


# ===== File Upload API =====

async def api_files_upload(request: web.Request) -> web.Response:
    """Upload a file.
    
    POST /api/files/upload
    Content-Type: multipart/form-data
    Body: file (binary)
    
    Returns:
        201: {"success": true, "file_id": "...", "filename": "...", ...}
        400: {"success": false, "error": "..."}
    """
    try:
        from src.utils.file_parser import upload_file, FileTooLargeError, UnsupportedFileTypeError
        
        # Get session ID from query or header
        session_id = request.query.get('session_id') or request.headers.get('X-Session-ID')
        
        # Parse multipart form
        reader = await request.multipart()
        file_field = await reader.next()
        
        if not file_field or file_field.name != 'file':
            return web.json_response({
                'success': False,
                'error': 'No file provided'
            }, status=400)
        
        # Read file content
        max_size_mb = 10
        max_bytes = max_size_mb * 1024 * 1024
        
        try:
            content = await file_field.read()
        except TypeError:
            content_chunks = []
            total_size = 0
            while True:
                chunk = await file_field.read(8192)
                if not chunk:
                    break
                content_chunks.append(chunk)
                total_size += len(chunk)
                if total_size > max_bytes:
                    return web.json_response({
                        'success': False,
                        'error': f'File exceeds maximum size of {max_size_mb} MB'
                    }, status=400)
            content = b''.join(content_chunks)
        
        filename = file_field.filename
        
        if not filename:
            return web.json_response({
                'success': False,
                'error': 'Filename is required'
            }, status=400)
        
        logger.info(f"[api_files_upload] session_id={session_id}, filename={filename}")
        metadata = await upload_file(
            session_id=session_id,
            content=content,
            filename=filename,
            max_size_mb=max_size_mb
        )
        logger.info(f"[api_files_upload] saved metadata.session_id={metadata.session_id}")
        
        return web.json_response({
            'success': True,
            'file_id': metadata.file_id,
            'filename': metadata.original_filename,
            'content_type': metadata.content_type,
            'size': metadata.size,
            'uploaded_at': metadata.uploaded_at
        }, status=201)
        
    except FileTooLargeError as e:
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=400)
        
    except UnsupportedFileTypeError as e:
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=400)
        
    except Exception as e:
        logger.error(f"File upload error: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)


async def api_files_parse(request: web.Request) -> web.Response:
    """Parse a file.
    
    POST /api/files/parse
    Content-Type: application/json
    Body: {"file_id": "...", "options": {...}}
    
    Returns:
        200: {"success": true, "markdown": "...", "blocks": [...], ...}
        400: {"success": false, "error": "..."}
    """
    try:
        
        
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({
                'success': False,
                'error': 'Invalid JSON body'
            }, status=400)
        
        file_id = data.get('file_id')
        
        if not file_id:
            return web.json_response({
                'success': False,
                'error': 'file_id is required'
            }, status=400)
        
        # Validate session ownership
        session_id = request.query.get('session_id') or request.headers.get('X-Session-ID')
        try:
            metadata = get_metadata(file_id)
            # If the file is bound to a session, require a matching session_id
            if metadata.session_id:
                if not session_id or metadata.session_id != session_id:
                    return web.json_response({
                        'success': False,
                        'error': 'File not found'
                    }, status=404)
        except StoredFileNotFoundError:
            pass  # Will be caught below
        
        options = data.get('options', {})
        
        # Parse file
        result = await parse_file(file_id, options)
        
        # Save chunks to file context storage
        if result.success and result.blocks:
            try:
                from src.hooks.file_context import storage
                from src.hooks.file_context.models import Chunk, SessionFileMeta
                import hashlib
                
                # Update file status to processing
                storage.update_file_status(
                    session_id=session_id,
                    file_id=file_id,
                    status="processing"
                )
                
                # Save chunks
                total_chars = 0
                for block in result.blocks:
                    # Generate chunk_id if not present
                    chunk_id = block.get('chunk_id') or f"{file_id}_{block.get('type', 'chunk')}_{block.get('page', 1)}_{block.get('index', 1)}"
                    
                    # Compute content hash for deduplication
                    content = block.get('content', '')
                    content_hash = hashlib.sha256(content.encode()).hexdigest()
                    
                    chunk = Chunk(
                        chunk_id=chunk_id,
                        file_id=file_id,
                        session_id=session_id or '',
                        type=block.get('type', 'paragraph'),
                        content=content,
                        markdown=block.get('markdown'),
                        page=block.get('page'),
                        index=block.get('index', 1),
                        source=block.get('method', 'unknown'),
                        confidence=block.get('confidence', 0.95),
                        content_hash=content_hash
                    )
                    storage.save_chunk(chunk)
                    total_chars += len(content)
                
                # Update file status to completed
                storage.update_file_status(
                    session_id=session_id,
                    file_id=file_id,
                    status="completed",
                    chunk_count=len(result.blocks),
                    total_chars=total_chars
                )
                
                logger.info(f"[api_files_parse] Saved {len(result.blocks)} chunks for file {file_id}")
            except Exception as e:
                logger.error(f"[api_files_parse] Failed to save chunks: {e}")
                # Continue anyway, parse succeeded
        
        if not result.success:
            return web.json_response({
                'success': False,
                'error': result.error
            }, status=400)
        
        return web.json_response({
            'success': True,
            'content_type': result.content_type,
            'file_id': result.file_id,
            'filename': result.filename,
            'markdown': result.markdown,
            'blocks': [b.model_dump(by_alias=True, exclude_none=True) for b in result.blocks],
            'json': result.json,
            'parse_time_ms': result.parse_time_ms
        })
        
    except StoredFileNotFoundError as e:
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=404)
        
    except Exception as e:
        logger.error(f"File parse error: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)


async def api_files_preview(request: web.Request) -> web.Response:
    """Preview a file.
    
    GET /api/files/{file_id}/preview?max_chars=5000
    
    Returns:
        200: {"success": true, "preview": "...", "truncated": true, ...}
    """
    try:
        from src.utils.file_parser import preview_file, StoredFileNotFoundError, get_metadata
        
        file_id = request.match_info.get('file_id')
        
        # Validate max_chars
        max_chars_raw = request.query.get('max_chars', '5000')
        try:
            max_chars = int(max_chars_raw)
        except (TypeError, ValueError):
            return web.json_response({
                'success': False,
                'error': 'max_chars must be an integer'
            }, status=400)
        
        if not file_id:
            return web.json_response({
                'success': False,
                'error': 'file_id is required'
            }, status=400)
        
        # Validate session ownership
        session_id = request.query.get('session_id') or request.headers.get('X-Session-ID')
        try:
            metadata = get_metadata(file_id)
            # If the file is bound to a session, require a matching session_id
            if metadata.session_id:
                if not session_id or metadata.session_id != session_id:
                    return web.json_response({
                        'success': False,
                        'error': 'File not found'
                    }, status=404)
        except StoredFileNotFoundError:
            pass
        
        result = await preview_file(file_id, max_chars)
        
        if not result.get('success'):
            error_msg = str(result.get('error', '') or '')
            if 'not found' in error_msg.lower():
                return web.json_response(result, status=404)
            return web.json_response(result, status=400)
        
        return web.json_response(result)
        
    except StoredFileNotFoundError:
        return web.json_response({
            'success': False,
            'error': 'File not found'
        }, status=404)
        
    except Exception as e:
        logger.error(f"File preview error: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)


async def api_files_list(request: web.Request) -> web.Response:
    """List uploaded files.
    
    GET /api/files/list?session_id=xxx
    
    Returns:
        200: {"files": [...]}
        400: {"success": false, "error": "session_id is required"}
    """
    try:
        from src.utils.file_parser import list_files, init_storage
        init_storage()
        
        # Session_id is optional - if not provided, list all files
        session_id = request.query.get('session_id') or request.headers.get('X-Session-ID')
        
        files = list_files(session_id) if session_id else list_files()
        
        return web.json_response({
            'files': [
                {
                    'file_id': f.file_id,
                    'filename': f.original_filename,
                    'content_type': f.content_type,
                    'size': f.size,
                    'uploaded_at': f.uploaded_at
                }
                for f in files
            ]
        })
        
    except Exception as e:
        logger.error(f"File list error: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)


async def api_context_files(request: web.Request) -> web.Response:
    """List files in session context.
    
    GET /api/context/files?session_id=xxx
    
    Returns:
        200: {"files": [...]}
    """
    try:
        session_id = request.query.get('session_id')
        if not session_id:
            return web.json_response({
                'success': False,
                'error': 'session_id is required'
            }, status=400)
        
        from src.hooks.file_context import storage
        files = storage.get_session_files(session_id)
        
        return web.json_response({
            'success': True,
            'files': [
                {
                    'file_id': f.file_id,
                    'filename': f.filename,
                    'content_type': f.content_type,
                    'parse_status': f.parse_status,
                    'chunk_count': f.chunk_count,
                    'total_chars': f.total_chars,
                    'parsed_at': f.parsed_at
                }
                for f in files
            ]
        })
    except Exception as e:
        logger.error(f"Error listing context files: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)


async def api_chunks_search(request: web.Request) -> web.Response:
    """Search chunks in session.
    
    GET /api/chunks/search?session_id=xxx&query=revenue&top_k=5
    
    Returns:
        200: {"chunks": [...], "total": N}
    """
    try:
        session_id = request.query.get('session_id')
        if not session_id:
            return web.json_response({
                'success': False,
                'error': 'session_id is required'
            }, status=400)
        
        query = request.query.get('query', '')
        top_k = int(request.query.get('top_k', 5))
        
        from src.hooks.file_context import retrieval_engine
        from src.hooks.file_context.models import RetrievalRequest
        
        result = retrieval_engine.retrieve(RetrievalRequest(
            session_id=session_id,
            query=query,
            top_k=top_k
        ))
        
        return web.json_response({
            'success': True,
            'chunks': [
                {
                    'chunk_id': c.chunk_id,
                    'file_id': c.file_id,
                    'type': c.type,
                    'content': c.content[:500],  # Truncate for preview
                    'page': c.page,
                    'confidence': c.confidence
                }
                for c in result.chunks
            ],
            'total': result.total_chunks,
            'estimated_tokens': result.estimated_tokens
        })
    except Exception as e:
        logger.error(f"Error searching chunks: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)


async def api_files_get(request: web.Request) -> web.Response:
    """Get a file (for direct display in img, etc).
    
    GET /api/files/{file_id}
    
    Returns:
        200: File content with appropriate Content-Type
        404: File not found
    """
    try:
        from src.utils.file_parser import get_file_path, get_metadata, StoredFileNotFoundError
        
        file_id = request.match_info.get('file_id')
        
        if not file_id:
            return web.json_response({
                'success': False,
                'error': 'file_id is required'
            }, status=400)
        
        try:
            metadata = get_metadata(file_id)
        except StoredFileNotFoundError:
            return web.json_response({
                'success': False,
                'error': 'File not found'
            }, status=404)
        
        file_path = get_file_path(file_id)
        
        if not file_path.exists():
            return web.json_response({
                'success': False,
                'error': 'File not found on disk'
            }, status=404)
        
        # Determine content type
        content_type = metadata.content_type or 'application/octet-stream'
        
        # Read and return file
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        return web.Response(
            body=file_content,
            content_type=content_type,
            headers={
                'Content-Disposition': f'inline; filename="{metadata.original_filename}"'
            }
        )
        
    except Exception as e:
        logger.error(f"File get error: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)




    """Get a file by ID.
    
    GET /api/files/{file_id}
    
    Returns:
        200: The file content
        404: File not found
    """
    try:
        from src.utils.file_parser import get_file_path, get_metadata, StoredFileNotFoundError
        
        file_id = request.match_info.get('file_id')
        
        if not file_id:
            return web.json_response({
                'success': False,
                'error': 'file_id is required'
            }, status=400)
        
        try:
            file_path = get_file_path(file_id)
        except StoredFileNotFoundError:
            return web.json_response({
                'success': False,
                'error': 'File not found'
            }, status=404)
        
        metadata = get_metadata(file_id)
        
        # Determine content type
        content_type = metadata.content_type or 'application/octet-stream'
        if not content_type or content_type == 'application/octet-stream':
            # Try to detect from extension
            import mimetypes
            content_type = mimetypes.guess_type(metadata.filename)[0] or 'application/octet-stream'
        
        # Read and return file
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        return web.Response(
            body=file_content,
            content_type=content_type,
            headers={
                'Content-Disposition': f'inline; filename="{metadata.filename}"'
            }
        )
    except Exception as e:
        logger.error(f"Error getting file {file_id}: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)


async def api_files_download(request: web.Request) -> web.Response:
    """Download files.
    
    GET /api/files/download?paths=<file_path>
    GET /api/files/download?paths=<file1>&paths=<file2>&...  (multiple)
    
    Returns:
        200: File or ZIP archive
        404: File not found
    """
    try:
        import io
        import os
        import zipfile
        from pathlib import Path
        from typing import List
        
        # Get file paths from query param (support multiple 'paths')
        # Collect all 'paths' and 'path' values from query string
        file_paths = []
        for key, value in request.query.items():
            if key == 'paths' or key == 'path':
                if value:
                    file_paths.append(value)
        
        if not file_paths:
            return web.json_response({
                'success': False,
                'error': 'path is required'
            }, status=400)
        
        # Security: restrict paths to workspace directory
        workspace_root = Path.home() / ".efp" / "workspace"
        
        def is_safe_path(path: str) -> bool:
            """Check if path is within workspace directory."""
            try:
                resolved = Path(path).resolve()
                # Check if resolved path is within workspace
                return str(resolved).startswith(str(workspace_root))
            except Exception:
                return False
        
        # Validate all paths are within workspace
        for fp in file_paths:
            if not is_safe_path(fp):
                return web.json_response({
                    'success': False,
                    'error': 'Path must be within workspace directory'
                }, status=400)
        
        # Handle single file or directory
        if len(file_paths) == 1:
            file_path = file_paths[0]
            try:
                resolved_path = Path(file_path).resolve()
            except Exception:
                return web.json_response({
                    'success': False,
                    'error': 'Invalid path'
                }, status=400)
            
            if not resolved_path.exists():
                return web.json_response({
                    'success': False,
                    'error': 'File not found'
                }, status=404)
            
            # If it's a directory, create a ZIP of the entire directory
            if resolved_path.is_dir():
                def create_dir_zip():
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for root, dirs, files in os.walk(resolved_path):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, resolved_path.parent)
                                zf.write(file_path, arcname)
                    return buffer.getvalue()
                
                content = await asyncio.to_thread(create_dir_zip)
                
                response = web.Response(
                    body=content,
                    content_type='application/zip',
                    headers={
                        'Content-Disposition': f'attachment; filename="{resolved_path.name}.zip"'
                    }
                )
                return response
            
            # Single file download
            if not resolved_path.is_file():
                return web.json_response({
                    'success': False,
                    'error': 'File not found'
                }, status=404)
            
            # Determine content type
            content_type = 'application/octet-stream'
            suffix = resolved_path.suffix.lower()
            if suffix == '.md':
                content_type = 'text/markdown'
            elif suffix == '.txt':
                content_type = 'text/plain'
            elif suffix == '.json':
                content_type = 'application/json'
            elif suffix == '.py':
                content_type = 'text/x-python'
            elif suffix in ['.jpg', '.jpeg']:
                content_type = 'image/jpeg'
            elif suffix == '.png':
                content_type = 'image/png'
            elif suffix == '.pdf':
                content_type = 'application/pdf'
            
            # Read file synchronously
            def read_file():
                with open(resolved_path, 'rb') as f:
                    return f.read()
            
            content = await asyncio.to_thread(read_file)
            
            # Return single file
            response = web.Response(
                body=content,
                content_type=content_type,
                headers={
                    'Content-Disposition': f'attachment; filename="{resolved_path.name}"'
                }
            )
            return response
        
        # Handle multiple files - create ZIP (including directories)
        def create_zip():
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file_path in file_paths:
                    try:
                        resolved_path = Path(file_path).resolve()
                        if not resolved_path.exists():
                            continue
                        if resolved_path.is_dir():
                            # Add entire directory
                            for root, dirs, files in os.walk(resolved_path):
                                for file in files:
                                    full_path = os.path.join(root, file)
                                    arcname = os.path.relpath(full_path, resolved_path.parent)
                                    zf.write(full_path, arcname)
                        elif resolved_path.is_file():
                            zf.write(resolved_path, resolved_path.name)
                    except Exception:
                        continue
            return buffer.getvalue()
        
        content = await asyncio.to_thread(create_zip)
        
        # Return ZIP file
        response = web.Response(
            body=content,
            content_type='application/zip',
            headers={
                'Content-Disposition': 'attachment; filename="files.zip"'
            }
        )
        return response
        
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)


async def api_files_delete(request: web.Request) -> web.Response:
    """Delete a file.
    
    DELETE /api/files/{file_id}
    
    Returns:
        200: {"success": true}
    """
    try:
        from src.utils.file_parser.storage import init_storage, delete_file, get_metadata, StoredFileNotFoundError
        init_storage()
        
        file_id = request.match_info.get('file_id')
        
        if not file_id:
            return web.json_response({
                'success': False,
                'error': 'file_id is required'
            }, status=400)
        
        deleted = delete_file(file_id)
        
        if not deleted:
            return web.json_response({
                'success': False,
                'error': 'File not found'
            }, status=404)
        
        return web.json_response({'success': True})
        
    except Exception as e:
        logger.error(f"File delete error: {e}")
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)


def setup_webchat_routes(app: web.Application):
    """Set up WebChat routes.
    
    Routes:
        GET  /             - WebChat UI (root)
        GET  /chat         - WebChat UI
        GET  /static/*     - Static files (CSS, JS)
        POST /api/chat     - Send message
        POST /api/chat/stream - Send message (streaming SSE)
        POST /api/tasks/execute - Execute structured runtime task
        GET  /api/sessions - List recent sessions
        GET  /api/sessions/{session_id} - Load session messages
        GET  /api/files    - Browse files
        GET  /api/usage   - Get usage stats
        POST /api/clear   - Clear session
        GET  /api/skills  - Get available skills
    """
    app.router.add_get('/', serve_webchat)
    
    app.router.add_get('/static/{path:.*}', serve_static)
    app.router.add_post('/api/chat', api_chat)
    app.router.add_post('/api/chat/stream', api_chat_stream)
    app.router.add_post('/api/tasks/execute', api_tasks_execute)
    app.router.add_get('/api/sessions', api_sessions)
    app.router.add_get('/api/sessions/{session_id}', api_load_session)
    app.router.add_get('/api/sessions/{session_id}/chatlog', api_session_chatlog)
    app.router.add_get('/api/files', api_browse_files)
    app.router.add_get('/api/files/read', api_read_file)
    app.router.add_get('/api/usage', api_usage)
    app.router.add_post('/api/clear', api_clear)
    app.router.add_post('/api/sessions/{session_id}/messages/{message_id}/edit', api_edit_message)
    app.router.add_post('/api/sessions/{session_id}/messages/{message_id}/delete-from-here', api_delete_conversation_from)
    app.router.add_get('/api/config', api_get_config)
    app.router.add_post('/api/config/save', api_save_config)
    app.router.add_post('/api/ssh/generate', api_ssh_generate)
    app.router.add_get('/api/ssh/public-key', api_ssh_public_key)
    app.router.add_get('/api/skills', api_skills)
    app.router.add_post('/api/copilot/auth/start', api_copilot_auth_start)
    app.router.add_post('/api/copilot/auth/check', api_copilot_auth_check)
    
    # File upload/parse routes
    app.router.add_post('/api/files/upload', api_files_upload)
    app.router.add_post('/api/files/parse', api_files_parse)
    app.router.add_get('/api/files/list', api_files_list)
    app.router.add_get('/api/files/download', api_files_download)
    app.router.add_get('/api/files/{file_id}/preview', api_files_preview)
    app.router.add_get('/api/files/{file_id}', api_files_get)
    app.router.add_delete('/api/files/{file_id}', api_files_delete)

    # Basic sanity check to ensure the GET /api/files/{file_id} route stays registered.
    # This helps catch regressions if the route is removed or renamed without updating tests.
    assert any(
        route.method == 'GET'
        and getattr(route.resource, 'canonical', None) == '/api/files/{file_id}'
        for route in app.router.routes()
    ), "Expected GET /api/files/{file_id} route to be registered"
    
    # File context API endpoints
    app.router.add_get('/api/context/files', api_context_files)
    app.router.add_get('/api/chunks/search', api_chunks_search)
    
    logger.info("WebChat routes registered:")
    logger.info("  GET  /              - WebChat UI (root)")
    
    logger.info("  GET  /static/*     - Static files (CSS, JS)")
    logger.info("  POST /api/chat     - Send message")
    logger.info("  POST /api/chat/stream - Send message (streaming SSE)")
    logger.info("  POST /api/tasks/execute - Execute structured runtime task")
    logger.info("  GET  /api/sessions - List recent sessions")
    logger.info("  GET  /api/sessions/{id} - Load session messages")
    logger.info("  GET  /api/files    - Browse files")
    logger.info("  GET  /api/files/read - Read file content")
    logger.info("  GET  /api/usage   - Get usage stats")
    logger.info("  POST /api/clear   - Clear session")
    logger.info("  GET  /api/skills  - Get available skills")
