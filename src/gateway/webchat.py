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
import time
import io
import mimetypes
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Set
from aiohttp import web, ContentTypeError
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.file_parser.storage import init_storage, _file_metadata, StoredFileNotFoundError, get_metadata
init_storage()
from src.utils.file_parser import parse_file
from src.utils.truncate import truncate
from src.utils.redaction import safe_preview, safe_log_field, sanitize_exception_message
from src.utils.logger import clear_log_context, set_log_context


from src.agents.core import Agent as AgentCore
from src.agents.core import run_chat_execution
from src.hooks.session_memory import save_session_summary
from src.agents.errors import extract_error_details, LLMError
from src.hooks.file_context import inject_context
from src.config import config as global_config
from src.github.url_utils import normalize_github_api_base_url
from src.runtime.chat_orchestration_adapter import execute_chat_orchestration, execute_runtime_task_request
from src.runtime.runtime_task_tracker import RuntimeTaskTracker
from src.runtime.portal_session_metadata_client import (
    extract_session_metadata_publish_fields,
    publish_session_metadata,
)
from src.gateway.chat_payloads import (
    build_webchat_response_payload,
    normalize_assistant_history_message,
)
from src.gateway.webchat_request_contracts import (
    build_stream_start_event_payload,
    extract_trusted_client_request_id,
)
from src.runtime.capability_registry import get_capability_registry
from src.gateway.event_bus import emit_agent_event
from src.sessions.manager import resolve_session_display_name, session_manager
from src.sessions.persistence import session_persistence
from src.sessions.usage import usage_tracker

logger = logging.getLogger(__name__)
runtime_task_tracker = RuntimeTaskTracker()


# Get template and static paths
TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
MAX_PORTAL_IDENTITY_LENGTH = 256
_DERIVED_RUNTIME_RULE_KEYS = {
    "allowed_capability_ids",
    "allowed_capability_types",
    "denied_capability_ids",
    "denied_capability_types",
    "allowed_external_systems",
    "allowed_webhook_triggers",
    "allowed_actions",
    "allowed_adapter_actions",
    "denied_actions",
    "denied_adapter_actions",
    "governance_require_explicit_allow",
    "governance_allow_auto_run",
    "governance_external_allowlist",
    "governance_external_blocklist",
}


def _sanitize_portal_identity_value(value: Any) -> str:
    raw = "" if value is None else str(value)
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", raw).strip()
    return cleaned[:MAX_PORTAL_IDENTITY_LENGTH]


def _extract_portal_identity(request: web.Request, data: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    headers = getattr(request, "headers", {}) or {}
    header_user_id = _sanitize_portal_identity_value(headers.get("X-Portal-User-Id"))
    header_user_name = _sanitize_portal_identity_value(headers.get("X-Portal-User-Name"))

    if not _is_trusted_portal_request(request):
        logger.debug("[portal_identity] resolved_source=untrusted has_user_id=False has_user_name=False")
        return None, None

    resolved_user_id = header_user_id or None
    resolved_user_name = header_user_name or None

    if header_user_id or header_user_name:
        source = "trusted_headers"
    else:
        source = "trusted_none"
    logger.debug("[portal_identity] resolved_source=%s has_user_id=%s has_user_name=%s", source, bool(resolved_user_id), bool(resolved_user_name))
    return resolved_user_id, resolved_user_name


def _extract_trusted_portal_agent_name(request: web.Request) -> Optional[str]:
    if not _is_trusted_portal_request(request):
        return None
    headers = getattr(request, "headers", {}) or {}
    agent_name = _sanitize_portal_identity_value(headers.get("X-Portal-Agent-Name"))
    return agent_name or None


def _is_trusted_portal_request(request: web.Request) -> bool:
    headers = getattr(request, "headers", {}) or {}
    portal_source = str(headers.get("X-Portal-Author-Source") or "").strip().lower()
    return portal_source == "portal"


def _resolve_chat_display_user_name(data: Dict[str, Any], portal_user_name: Optional[str]) -> str:
    direct_user_name = _sanitize_portal_identity_value(data.get("user_name")) or None
    return portal_user_name or direct_user_name or "webchat-user"


def _parse_optional_execution_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    if "metadata" not in data or data.get("metadata") is None:
        return {}
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    return dict(metadata)


def _extract_trusted_control_plane_metadata(request: web.Request, data: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _parse_optional_execution_metadata(data)
    if not _is_trusted_portal_request(request):
        return {}
    trusted_metadata = dict(metadata)
    policy_context = trusted_metadata.get("policy_context")
    if isinstance(policy_context, dict):
        derived_rules = policy_context.get("derived_runtime_rules")
        if isinstance(derived_rules, dict):
            for key, value in derived_rules.items():
                if key in _DERIVED_RUNTIME_RULE_KEYS:
                    trusted_metadata[key] = value
    return trusted_metadata


def _extract_trusted_client_request_id(request: web.Request, data: Dict[str, Any]) -> Optional[str]:
    return extract_trusted_client_request_id(_is_trusted_portal_request(request), data)


def _extract_trusted_model_override(request: web.Request, data: Dict[str, Any]) -> Optional[str]:
    if not _is_trusted_portal_request(request):
        return None
    value = data.get("model_override")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


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

    context_ref = data.get("context_ref")
    if context_ref is not None and not isinstance(context_ref, dict):
        raise ValueError("context_ref must be a JSON object")

    return {
        "task_id": task_id,
        "task_type": task_type,
        "input_payload": dict(input_payload),
        "session_id": _optional_string(data, "session_id"),
        "source": _optional_string(data, "source"),
        "workflow_rule_id": _optional_string(data, "workflow_rule_id"),
        "shared_context_ref": _optional_string(data, "shared_context_ref"),
        "context_ref": dict(context_ref or {}),
        "metadata": dict(metadata),
    }


def _sanitize_trace_value(value: Any, max_len: int = 128) -> Optional[str]:
    if value is None:
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", str(value)).strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


async def _collect_attached_images(
    *,
    session_id: str,
    message: str,
    attachments: Optional[List[str]],
) -> List[str]:
    attached_images: List[str] = []
    processed_file_ids: Set[str] = set()

    async def process_file(file_id: str) -> bool:
        if not isinstance(file_id, str) or not file_id:
            return False
        if file_id in processed_file_ids:
            return False
        try:
            from src.utils.file_parser.storage import get_file_path
            metadata = get_metadata(file_id)
            if metadata.session_id and metadata.session_id != session_id:
                logger.warning(f"[api_chat] File {file_id} belongs to different session")
                return False
            if metadata.content_type and metadata.content_type.startswith('image/'):
                file_path = get_file_path(file_id)
                if file_path.exists():
                    import base64
                    img_data = await asyncio.to_thread(
                        lambda: base64.b64encode(file_path.read_bytes()).decode('utf-8')
                    )
                    ext = metadata.content_type.split('/')[-1]
                    attached_images.append(f'data:image/{ext};base64,{img_data}')
                    processed_file_ids.add(file_id)
                    return True
        except StoredFileNotFoundError:
            logger.warning(f"[api_chat] File {file_id} not found")
        except Exception as e:
            logger.warning(f"[api_chat] Failed to process file {safe_preview(file_id, 80)}: {sanitize_exception_message(e)}")
        return False

    try:
        refs = re.findall(r'@file_([a-zA-Z0-9]+)', message)
        for short_id in dict.fromkeys(refs):
            try:
                metadata = get_metadata(short_id)
                await process_file(metadata.file_id)
            except (StoredFileNotFoundError, ValueError):
                from src.utils.file_parser.storage import find_file_by_prefix
                try:
                    fid = find_file_by_prefix(short_id)
                    if fid:
                        await process_file(fid)
                except ValueError as ve:
                    logger.warning(f"[api_chat] Prefix lookup failed: {sanitize_exception_message(ve)}")
    except Exception as e:
        logger.warning(f"[api_chat] @file_ parse error: {sanitize_exception_message(e)}")

    if attachments and isinstance(attachments, list):
        for file_id in attachments:
            await process_file(file_id)

    return attached_images


def _extract_task_trace_headers(request: web.Request) -> Dict[str, Optional[str]]:
    headers = getattr(request, "headers", {}) or {}
    trace = {
        "trace_id": _sanitize_trace_value(headers.get("X-Trace-Id")),
        "span_id": _sanitize_trace_value(headers.get("X-Span-Id")),
        "parent_span_id": _sanitize_trace_value(headers.get("X-Parent-Span-Id")),
        "portal_task_id": _sanitize_trace_value(headers.get("X-Portal-Task-Id")),
        "portal_dispatch_id": _sanitize_trace_value(headers.get("X-Portal-Dispatch-Id")),
    }
    if not trace["trace_id"]:
        trace["trace_id"] = f"rt-{uuid.uuid4().hex}"
    if not trace["span_id"]:
        trace["span_id"] = uuid.uuid4().hex[:16]
    return trace


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
    execution_metadata: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
    request_id: Optional[str] = None,
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

    merged_metadata = dict(execution_metadata or {})
    merged_metadata.pop("path", None)
    resolved_request_id = request_id or f"chat-{uuid.uuid4()}"
    execution_result = await execute_chat_orchestration(
        request_id=resolved_request_id,
        session_id=session_id,
        source_ref="webchat",
        agent_id=agent_id,
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
        metadata={
            "path": request_path,
            "persist_last_execution_id": True,
            **merged_metadata,
        },
        chat_handler=_chat_handler,
    )
    original_output_payload = execution_result.output_payload
    output_payload = dict(original_output_payload) if isinstance(original_output_payload, dict) else {}
    output_payload["request_id"] = getattr(execution_result, "request_id", resolved_request_id)
    output_payload["_execution_result"] = execution_result
    if execution_result.status == "error" or output_payload.get("error"):
        error_value = output_payload.get("error", "Execution bus error")
        structured_error = error_value if isinstance(error_value, dict) else {}
        status_code = None

        if structured_error:
            error_message = (
                structured_error.get("message")
                or structured_error.get("error")
                or json.dumps(structured_error, ensure_ascii=False)
            )
            error_type = structured_error.get("type")
            code = structured_error.get("code")
            details = structured_error.get("details")
            status_code = structured_error.get("status_code")
        else:
            error_message = str(error_value)
            error_type = output_payload.get("error_type")
            code = output_payload.get("code")
            details = output_payload.get("details")
            status_code = output_payload.get("status_code")

        response_body = {
            "error": error_message,
            "detail": error_message,
            "error_type": error_type if isinstance(error_type, str) else "",
            "code": code if isinstance(code, str) else "",
            "details": details if isinstance(details, dict) else {},
            "request_id": output_payload.get("request_id"),
        }
        resolved_status_code = status_code if isinstance(status_code, int) and 400 <= status_code <= 599 else 500

        if resolved_status_code == 500:
            raise web.HTTPInternalServerError(
                text=json.dumps(response_body, ensure_ascii=False),
                content_type="application/json",
            )

        class _StructuredHTTPError(web.HTTPException):
            status_code = resolved_status_code

        raise _StructuredHTTPError(
            text=json.dumps(response_body, ensure_ascii=False),
            content_type="application/json",
        )
    return output_payload


def _resolve_runtime_agent_identity(request: web.Request) -> tuple[Optional[str], Optional[str]]:
    """Resolve runtime agent identity from server-side state/config, never from client body."""
    runtime_agent_id: Optional[str] = None
    runtime_agent_name: Optional[str] = None

    app = getattr(request, "app", {}) or {}

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

    trusted_portal_agent_name = _extract_trusted_portal_agent_name(request)

    raw_agent_id = app.get("agent_id") if hasattr(app, "get") else None
    raw_agent_name = app.get("agent_name") if hasattr(app, "get") else None
    if raw_agent_id:
        runtime_agent_id = str(raw_agent_id).strip() or None
    if trusted_portal_agent_name:
        runtime_agent_name = trusted_portal_agent_name
    elif raw_agent_name:
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


def _normalize_chat_history_message(
    message: Dict[str, Any],
    *,
    portal_user_id: Optional[str],
    portal_user_name: Optional[str],
    runtime_agent_id: Optional[str],
    runtime_agent_name: Optional[str],
    trusted_portal_agent_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize history message fields for display blocks and author metadata."""
    if not isinstance(message, dict):
        return message

    normalized_message = dict(message)
    role = normalized_message.get("role")

    if role == "assistant":
        normalized_message = normalize_assistant_history_message(normalized_message)
        normalized_message.setdefault("author_type", "agent")
        normalized_message.setdefault("author_source", "runtime")
        if runtime_agent_id and not normalized_message.get("author_id"):
            normalized_message["author_id"] = runtime_agent_id
        if runtime_agent_name and not normalized_message.get("author_name"):
            normalized_message["author_name"] = runtime_agent_name

        if trusted_portal_agent_name and runtime_agent_name == trusted_portal_agent_name:
            author_type = normalized_message.get("author_type")
            author_id = normalized_message.get("author_id")
            if (author_type in (None, "agent")) and (
                not author_id or (runtime_agent_id and author_id == runtime_agent_id)
            ):
                normalized_message["author_name"] = runtime_agent_name
        return normalized_message

    if role == "user":
        normalized_message.setdefault("author_type", "human")
        if portal_user_id and not normalized_message.get("author_id"):
            normalized_message["author_id"] = portal_user_id
        if portal_user_name and not normalized_message.get("author_name"):
            normalized_message["author_name"] = portal_user_name
        if not normalized_message.get("author_source"):
            normalized_message["author_source"] = "portal" if (portal_user_id or portal_user_name) else "runtime"
    return normalized_message


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
        execution_metadata = _extract_trusted_control_plane_metadata(request, data)
        client_request_id = _extract_trusted_client_request_id(request, data)
        request_id = client_request_id or f"chat-{uuid.uuid4()}"
        effective_user_name = _resolve_chat_display_user_name(data, portal_user_name)
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
        
        attached_images = await _collect_attached_images(
            session_id=session_id,
            message=message,
            attachments=attachments if isinstance(attachments, list) else None,
        )
        
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
        
        # Get request-scoped model (trusted portal override only)
        model_override = _extract_trusted_model_override(request, data)
        model = model_override or global_config.llm.get('model', 'gpt-5-mini')
        
        # Run agent (history is managed internally by session_manager)
        runtime_agent_id, runtime_agent_name = _resolve_runtime_agent_identity(request)
        agent = AgentCore(
            model=model,
            session_id=session_id,
            agent_id=runtime_agent_id,
            agent_name=runtime_agent_name,
        )
        if runtime_agent_id and session_id:
            try:
                await publish_session_metadata(
                    agent_id=runtime_agent_id,
                    session_id=session_id,
                    last_execution_id=request_id,
                    latest_event_type="chat.started",
                    latest_event_state="running",
                    snapshot_version=None,
                    runtime_events=[],
                    metadata=execution_metadata,
                )
            except Exception:
                logger.warning("Best-effort session metadata publish failed for chat.started", exc_info=True)
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
            execution_metadata=execution_metadata,
            agent_id=runtime_agent_id,
            request_id=request_id,
        )
        execution_result = result.get("_execution_result")
        if runtime_agent_id and execution_result is not None:
            publish_fields = extract_session_metadata_publish_fields(
                execution_result,
                metadata=execution_metadata,
                default_event_type="chat.completed",
                default_state="success",
            )
            try:
                await publish_session_metadata(
                    agent_id=runtime_agent_id,
                    session_id=session_id,
                    **publish_fields,
                )
            except Exception:
                logger.warning("Best-effort session metadata publish failed for chat path", exc_info=True)
        
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
        
        response_data = build_webchat_response_payload(
            result if isinstance(result, dict) else None,
            session_id,
        )
        usage = response_data.get("usage", {}) or {}
        
        # Record usage if available
        if usage:
            provider = global_config.llm.get('provider', 'openai')
            actual_model = (
                ((response_data.get("_llm_debug") or {}).get("request") or {}).get("model")
                or model
            )
            usage_tracker.record_usage(
                provider=provider,
                model=actual_model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                session_id=session_id,
                task_type="chat"
            )
        
        # Include events for thinking process display
        events = response_data.get("events", [])
        if events:
            # Save events to session for persistence
            if 'metadata' not in session:
                session['metadata'] = {}
            session['metadata']['thinking_events'] = events
            logger.info(f"[api_chat] Saved {len(events)} thinking events to session metadata")
        
        # Include LLM debug info for sidebar display
        llm_debug = response_data.get("_llm_debug", {}) or {}
        
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
        
        return web.json_response(response_data)
        
    except json.JSONDecodeError:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    except ValueError as e:
        return web.json_response({'error': str(e)}, status=400)
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
    response: Optional[web.StreamResponse] = None
    run_task: Optional[asyncio.Task] = None
    try:
        data = await request.json()
        message = (data.get('message') or '').strip()
        session_id = data.get('session_id', f'webchat_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}')
        attachments = data.get('attachments')
        portal_user_id, portal_user_name = _extract_portal_identity(request, data)
        execution_metadata = _extract_trusted_control_plane_metadata(request, data)
        client_request_id = _extract_trusted_client_request_id(request, data)
        request_id = client_request_id or f"chat-{uuid.uuid4()}"
        effective_user_name = _resolve_chat_display_user_name(data, portal_user_name)

        attached_images = await _collect_attached_images(
            session_id=session_id,
            message=message,
            attachments=attachments if isinstance(attachments, list) else None,
        )
        if attached_images and not message.strip():
            message = "[image]"

        if not message.strip() and not attached_images:
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
        await response.write(
            f"event: start\ndata: {json.dumps(build_stream_start_event_payload(session_id, request_id))}\n\n".encode()
        )

        event_queue = asyncio.Queue()

        # Get request-scoped model (trusted portal override only)
        model_override = _extract_trusted_model_override(request, data)
        model = model_override or global_config.llm.get('model', 'gpt-5-mini')

        # Run agent and stream response
        runtime_agent_id, runtime_agent_name = _resolve_runtime_agent_identity(request)
        agent = AgentCore(
            model=model,
            session_id=session_id,
            agent_id=runtime_agent_id,
            agent_name=runtime_agent_name,
        )
        if runtime_agent_id and session_id:
            try:
                await publish_session_metadata(
                    agent_id=runtime_agent_id,
                    session_id=session_id,
                    last_execution_id=request_id,
                    latest_event_type="chat.started",
                    latest_event_state="running",
                    snapshot_version=None,
                    runtime_events=[],
                    metadata=execution_metadata,
                )
            except Exception:
                logger.warning("Best-effort session metadata publish failed for streaming chat.started", exc_info=True)

        run_task = asyncio.create_task(
            _run_chat_via_execution_bus(
                agent=agent,
                message=message,
                session_id=session_id,
                user_name=effective_user_name,
                portal_user_id=portal_user_id,
                portal_user_name=portal_user_name,
                stream_callback=event_queue,
                attached_images=attached_images if attached_images else None,
                attachments=attachments if attachments else None,
                request_path="/api/chat/stream",
                execution_metadata=execution_metadata,
                agent_id=runtime_agent_id,
                request_id=request_id,
            )
        )

        while not run_task.done() or not event_queue.empty():
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.15)
                escaped = str(event).replace('\n', '\\n').replace('\r', '\\r')
                await response.write(f"event: progress\ndata: {escaped}\n\n".encode())
            except asyncio.TimeoutError:
                continue

        result = await run_task
        execution_result = result.get("_execution_result")
        if runtime_agent_id and execution_result is not None:
            publish_fields = extract_session_metadata_publish_fields(
                execution_result,
                metadata=execution_metadata,
                default_event_type="chat.completed",
                default_state="success",
            )
            try:
                await publish_session_metadata(
                    agent_id=runtime_agent_id,
                    session_id=session_id,
                    **publish_fields,
                )
            except Exception:
                logger.warning("Best-effort session metadata publish failed for streaming chat path", exc_info=True)

        usage = result.get("usage", {}) if result else {}

        # Record usage
        if usage:
            provider = global_config.llm.get('provider', 'openai')
            actual_model = (
                ((result.get("_llm_debug") or {}).get("request") or {}).get("model")
                or model
            )
            usage_tracker.record_usage(
                provider=provider,
                model=actual_model,
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
    except ValueError as e:
        response = web.json_response({'error': str(e)}, status=400)
        return response
    except web.HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stream error: {e}")
        if response is not None and run_task is not None:
            error_data = json.dumps({'error': str(e)})
            try:
                await response.write(f"event: error\ndata: {error_data}\n\n".encode())
                await response.write(f"event: done\ndata: \n\n".encode())
            except Exception:
                pass
            return response
        return web.Response(status=500, text=str(e))


async def api_tasks_execute(request: web.Request) -> web.Response:
    """Handle runtime task execution requests.

    POST /api/tasks/execute
    """
    clear_log_context()
    trace_headers = _extract_task_trace_headers(request)
    set_log_context(
        trace_id=trace_headers.get("trace_id"),
        span_id=trace_headers.get("span_id"),
        parent_span_id=trace_headers.get("parent_span_id"),
        portal_task_id=trace_headers.get("portal_task_id"),
        portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
        path="/api/tasks/execute",
    )
    try:
        data = await request.json()
        parsed = _parse_task_execute_request(data)
        set_log_context(
            request_id=f"task-{parsed['task_id']}",
            task_id=parsed["task_id"],
            portal_task_id=trace_headers.get("portal_task_id") or parsed["task_id"],
        )
        logger.debug(
            "Task execute request parsed | task_id=%s task_type=%s source=%s has_session_id=%s shared_context_ref=%s has_context_ref=%s metadata_keys=%s input_payload_keys=%s",
            parsed["task_id"],
            parsed["task_type"],
            parsed["source"] or "portal",
            bool(parsed["session_id"]),
            parsed["shared_context_ref"] or "-",
            bool(parsed["context_ref"]),
            sorted(parsed["metadata"].keys()),
            sorted(parsed["input_payload"].keys()),
        )
        merged_input_payload = dict(parsed["input_payload"])
        merged_input_payload["task_type"] = parsed["task_type"]
        # shared_context_ref is transported top-level and mirrored into input_payload for canonical task handler consumption.
        if parsed["shared_context_ref"]:
            merged_input_payload["shared_context_ref"] = parsed["shared_context_ref"]

        metadata = dict(parsed["metadata"])
        metadata["task_id"] = parsed["task_id"]
        metadata["trace_id"] = trace_headers.get("trace_id")
        metadata["span_id"] = trace_headers.get("span_id")
        metadata["parent_span_id"] = trace_headers.get("parent_span_id")
        metadata["portal_dispatch_id"] = trace_headers.get("portal_dispatch_id")
        metadata["portal_task_id"] = metadata.get("portal_task_id") or trace_headers.get("portal_task_id") or parsed["task_id"]
        metadata["path"] = "/api/tasks/execute"
        metadata["external_triggered"] = True
        metadata["auto_run"] = True
        metadata["governance_target"] = parsed["task_type"]
        if parsed["source"]:
            metadata["portal_task_source"] = parsed["source"]
        if parsed["workflow_rule_id"]:
            metadata["portal_workflow_rule_id"] = parsed["workflow_rule_id"]
        if parsed["shared_context_ref"]:
            metadata["shared_context_ref"] = parsed["shared_context_ref"]
        runtime_agent_id, _runtime_agent_name = _resolve_runtime_agent_identity(request)
        set_log_context(agent_id=runtime_agent_id)
        logger.info(
            "Task execute dispatch start | task_id=%s task_type=%s request_id=%s runtime_agent_id=%s",
            parsed["task_id"],
            parsed["task_type"],
            f"task-{parsed['task_id']}",
            runtime_agent_id or "-",
        )
        request_id = f"task-{parsed['task_id']}"
        runtime_task_tracker.create_pending(
            task_id=parsed["task_id"],
            request_id=request_id,
            task_type=parsed["task_type"],
            source=parsed["source"] or "portal",
            session_id=parsed["session_id"],
            agent_id=runtime_agent_id,
            trace_id=trace_headers.get("trace_id"),
            portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
            portal_task_id=metadata.get("portal_task_id"),
        )

        background_coro = _run_task_execution_in_background(
            task_id=parsed["task_id"],
            request_id=request_id,
            task_type=parsed["task_type"],
            session_id=parsed["session_id"],
            source=parsed["source"] or "portal",
            runtime_agent_id=runtime_agent_id,
            context_ref=parsed["context_ref"] or None,
            merged_input_payload=merged_input_payload,
            metadata=metadata,
            trace_headers=trace_headers,
        )
        try:
            background_task = _spawn_runtime_background_task(background_coro)
        except Exception as exc:
            background_coro.close()
            runtime_task_tracker.remove(parsed["task_id"])
            logger.error("Task execute scheduling failed | task_id=%s", parsed["task_id"], exc_info=True)
            logger.debug("Task execute scheduling error detail: %s", sanitize_exception_message(exc))
            return web.json_response({"error": "Internal server error"}, status=500)

        runtime_task_tracker.set_background_task(parsed["task_id"], background_task)
        await _emit_task_lifecycle_event(
            "task.accepted",
            task_id=parsed["task_id"],
            portal_task_id=metadata.get("portal_task_id"),
            agent_id=runtime_agent_id,
            session_id=parsed["session_id"],
            trace_id=trace_headers.get("trace_id"),
            portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
        )
        return web.json_response(
            {
                "ok": True,
                "accepted": True,
                "task_id": parsed["task_id"],
                "execution_type": "task",
                "request_id": request_id,
                "status": "accepted",
                "trace_id": trace_headers.get("trace_id"),
                "portal_dispatch_id": trace_headers.get("portal_dispatch_id"),
            },
            status=202,
        )
    except (json.JSONDecodeError, ContentTypeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.error("Task execution API error: %s", sanitize_exception_message(exc), exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)
    finally:
        clear_log_context()


def _spawn_runtime_background_task(coro: Any) -> asyncio.Task:
    return asyncio.create_task(coro)


async def _emit_task_lifecycle_event(
    event_type: str,
    *,
    task_id: str,
    portal_task_id: Optional[str],
    agent_id: Optional[str],
    session_id: Optional[str],
    trace_id: Optional[str],
    portal_dispatch_id: Optional[str],
) -> None:
    await emit_agent_event(
        event_type,
        {
            "task_id": task_id,
            "portal_task_id": portal_task_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "trace_id": trace_id,
            "portal_dispatch_id": portal_dispatch_id,
        },
    )


def _build_runtime_task_terminal_payload(
    *,
    task_id: str,
    execution_result: Any,
    trace_headers: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    status = execution_result.status
    output_payload = execution_result.output_payload
    response_payload: Dict[str, Any] = {
        "ok": status == "success",
        "task_id": task_id,
        "execution_type": "task",
        "request_id": execution_result.request_id,
        "status": status,
        "output_payload": _json_compatible(output_payload),
        "artifacts": _json_compatible(execution_result.artifacts),
        "runtime_events": _json_compatible(execution_result.runtime_events),
        "next_action_hint": execution_result.next_action_hint,
        "audit_ref": execution_result.audit_ref,
        "trace_id": trace_headers.get("trace_id"),
        "portal_dispatch_id": trace_headers.get("portal_dispatch_id"),
    }
    if status in {"error", "blocked"}:
        if isinstance(output_payload, dict):
            response_payload["error"] = output_payload.get("error") or output_payload
        else:
            response_payload["error"] = str(output_payload)
    return response_payload


async def _run_task_execution_in_background(
    *,
    task_id: str,
    request_id: str,
    task_type: str,
    session_id: Optional[str],
    source: str,
    runtime_agent_id: Optional[str],
    context_ref: Optional[Dict[str, Any]],
    merged_input_payload: Dict[str, Any],
    metadata: Dict[str, Any],
    trace_headers: Dict[str, Optional[str]],
) -> None:
    execution_started_at = time.perf_counter()
    runtime_task_tracker.mark_running(task_id)
    await _emit_task_lifecycle_event(
        "task.started",
        task_id=task_id,
        portal_task_id=metadata.get("portal_task_id"),
        agent_id=runtime_agent_id,
        session_id=session_id,
        trace_id=trace_headers.get("trace_id"),
        portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
    )
    try:
        execution_result = await execute_runtime_task_request(
            request_id=request_id,
            source_type="task",
            source_ref=source,
            execution_type="task",
            session_id=session_id,
            agent_id=runtime_agent_id,
            context_ref=context_ref,
            input_payload=merged_input_payload,
            metadata=metadata,
        )
        if runtime_agent_id and session_id:
            publish_fields = extract_session_metadata_publish_fields(
                execution_result,
                metadata=metadata,
                default_event_type="task.completed" if execution_result.status == "success" else "task.failed",
                default_state="success" if execution_result.status == "success" else "error",
            )
            try:
                await publish_session_metadata(
                    agent_id=runtime_agent_id,
                    session_id=session_id,
                    **publish_fields,
                )
            except Exception:
                logger.warning("Best-effort session metadata publish failed for task execution path", exc_info=True)

        response_payload = _build_runtime_task_terminal_payload(
            task_id=task_id,
            execution_result=execution_result,
            trace_headers=trace_headers,
        )
        status = str(execution_result.status or "error")
        runtime_task_tracker.mark_terminal(
            task_id,
            status=status,
            payload=response_payload,
            error_message=str(response_payload.get("error") or "") or None,
        )
        await _emit_task_lifecycle_event(
            "task.completed" if status == "success" else "task.failed",
            task_id=task_id,
            portal_task_id=metadata.get("portal_task_id"),
            agent_id=runtime_agent_id,
            session_id=session_id,
            trace_id=trace_headers.get("trace_id"),
            portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
        )
        logger.info(
            "Task execute dispatch end | status=%s task_type=%s runtime_events_count=%s artifacts_type=%s has_next_action_hint=%s duration_ms=%s",
            status,
            task_type,
            len(execution_result.runtime_events or []),
            type(execution_result.artifacts).__name__,
            bool(execution_result.next_action_hint),
            int((time.perf_counter() - execution_started_at) * 1000),
        )
    except Exception as exc:
        sanitized_message = sanitize_exception_message(exc)
        logger.error("Task execution background error: %s", sanitized_message, exc_info=True)
        failure_payload = {
            "ok": False,
            "task_id": task_id,
            "execution_type": "task",
            "request_id": request_id,
            "status": "error",
            "trace_id": trace_headers.get("trace_id"),
            "portal_dispatch_id": trace_headers.get("portal_dispatch_id"),
            "error": sanitized_message,
        }
        runtime_task_tracker.mark_internal_failure(
            task_id,
            payload=failure_payload,
            error_message=sanitized_message,
        )
        await _emit_task_lifecycle_event(
            "task.failed",
            task_id=task_id,
            portal_task_id=metadata.get("portal_task_id"),
            agent_id=runtime_agent_id,
            session_id=session_id,
            trace_id=trace_headers.get("trace_id"),
            portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
        )


async def api_task_status(request: web.Request) -> web.Response:
    clear_log_context()
    trace_headers = _extract_task_trace_headers(request)
    set_log_context(
        trace_id=trace_headers.get("trace_id"),
        span_id=trace_headers.get("span_id"),
        parent_span_id=trace_headers.get("parent_span_id"),
        portal_task_id=trace_headers.get("portal_task_id"),
        portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
        path="/api/tasks/{task_id}",
    )
    try:
        task_id = str(request.match_info.get("task_id") or "").strip()
        if not task_id:
            return web.json_response({"error": "task_id is required"}, status=400)
        record = runtime_task_tracker.get(task_id)
        if record is None:
            return web.json_response({"error": "Task not found"}, status=404)
        if record.status in {"accepted", "running"}:
            payload = {
                "ok": True,
                "task_id": record.task_id,
                "execution_type": "task",
                "request_id": record.request_id,
                "status": record.status,
                "trace_id": record.trace_id,
                "portal_dispatch_id": record.portal_dispatch_id,
                "accepted_at": record.accepted_at,
                "started_at": record.started_at,
                "finished_at": record.finished_at,
            }
            return web.json_response(payload)
        payload = dict(record.payload)
        payload["accepted_at"] = record.accepted_at
        payload["started_at"] = record.started_at
        payload["finished_at"] = record.finished_at
        return web.json_response(payload)
    finally:
        clear_log_context()


def _parse_bool_query(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    return None


async def api_capabilities(request: web.Request) -> web.Response:
    """List runtime capability catalog.

    GET /api/capabilities?type=...&enabled=true|false&capability_id=...
    """
    try:
        registry = get_capability_registry()
        snapshot = (
            registry.export_catalog_snapshot()
            if hasattr(registry, "export_catalog_snapshot")
            else {
                "capabilities": registry.export_catalog(),
                "count": len(registry.export_catalog()),
                "catalog_version": "legacy",
                "generated_at": datetime.utcnow().isoformat() + "Z",
            }
        )
        catalog = list(snapshot.get("capabilities") or [])

        capability_id = str(request.query.get("capability_id") or "").strip().lower()
        if capability_id:
            catalog = [item for item in catalog if str(item.get("capability_id") or "").lower() == capability_id]

        capability_type = str(request.query.get("type") or "").strip().lower()
        if capability_type:
            catalog = [item for item in catalog if str(item.get("type") or "").lower() == capability_type]

        enabled_filter = _parse_bool_query(request.query.get("enabled"))
        if enabled_filter is not None:
            catalog = [item for item in catalog if bool(item.get("enabled")) is enabled_filter]

        return web.json_response(
            {
                "capabilities": catalog,
                "count": len(catalog),
                "catalog_version": snapshot.get("catalog_version"),
                "generated_at": snapshot.get("generated_at"),
                "supports_snapshot_contract": True,
                "runtime_contract_version": "phase4-capability-surface-v1",
            }
        )
    except Exception as exc:
        logger.error("Capabilities API error: %s", sanitize_exception_message(exc), exc_info=True)
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
            
            session_name = resolve_session_display_name(session_info)
            
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
        
        session_info = await session_manager.get_existing_session(session_id)
        
        if not session_info:
            return web.json_response({'error': 'Session not found'}, status=404)
        
        portal_user_id, portal_user_name = _extract_portal_identity(request, {})
        runtime_agent_id, runtime_agent_name = _resolve_runtime_agent_identity(request)
        trusted_portal_agent_name = _extract_trusted_portal_agent_name(request)

        history = session_info.get('history', [])
        normalized_history = [
            _normalize_chat_history_message(
                msg,
                portal_user_id=portal_user_id,
                portal_user_name=portal_user_name,
                runtime_agent_id=runtime_agent_id,
                runtime_agent_name=runtime_agent_name,
                trusted_portal_agent_name=trusted_portal_agent_name,
            )
            for msg in history
        ]
        
        session_name = resolve_session_display_name(session_info)
        
        return web.json_response({
            'session_id': session_id,
            'name': session_name,
            'messages': normalized_history,
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


def _workspace_root() -> Path:
    return (Path.home() / ".efp" / "workspace").resolve()


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        return path == root or root in path.parents
    except Exception:
        return False


def _resolve_server_file_path(raw_path: Optional[str]) -> Path:
    """Resolve user-supplied path against workspace root and enforce boundary."""
    root = _workspace_root()
    value = (raw_path or "").strip()
    if not value:
        candidate = root
    else:
        supplied = Path(value)
        if supplied.is_absolute():
            candidate = supplied.resolve(strict=False)
        else:
            candidate = (root / supplied).resolve(strict=False)

    if not _is_within_root(candidate, root):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Path must be within workspace root"}), content_type="application/json")
    return candidate


def _server_file_language(path: Path) -> str:
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
    return language_map.get(path.suffix.lower(), 'text')


def _server_file_content_type(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _create_server_files_zip(paths: List[Path]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for entry in paths:
            if entry.is_dir():
                for root, _, files in os.walk(entry):
                    for file_name in files:
                        file_path = Path(root) / file_name
                        arcname = file_path.relative_to(entry.parent)
                        zf.write(file_path, str(arcname))
            elif entry.is_file():
                zf.write(entry, entry.name)
    return buffer.getvalue()


async def api_server_files_browse(request: web.Request) -> web.Response:
    """Browse workspace files rooted at ~/.efp/workspace."""
    try:
        base_path = _resolve_server_file_path(request.query.get('path'))
        logger.debug("[server-files] browse path=%s", base_path)

        if not base_path.exists():
            return web.json_response({'error': 'Path not found', 'path': str(base_path)}, status=404)
        if not base_path.is_dir():
            return web.json_response({'error': 'Path is not a directory', 'path': str(base_path)}, status=400)

        items = []
        for item in sorted(base_path.iterdir(), key=lambda value: (not value.is_dir(), value.name.lower())):
            data = {
                'name': item.name,
                'path': str(item.resolve()),
                'is_dir': item.is_dir(),
                'is_file': item.is_file(),
            }
            if item.is_file():
                data['size'] = item.stat().st_size
            items.append(data)

        return web.json_response({
            'root_path': str(_workspace_root()),
            'path': str(base_path.resolve()),
            'items': items,
        })
    except web.HTTPBadRequest as exc:
        return web.json_response(json.loads(exc.text), status=400)
    except Exception as e:
        logger.error(f"Error browsing server files: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_server_files_read(request: web.Request) -> web.Response:
    """Read UTF-8 text files under workspace root."""
    try:
        file_path = _resolve_server_file_path(request.query.get('path'))
        if not file_path.exists():
            return web.json_response({'error': 'File not found', 'path': str(file_path)}, status=404)
        if not file_path.is_file():
            return web.json_response({'error': 'Not a file', 'path': str(file_path)}, status=400)

        try:
            content = file_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            return web.json_response({
                'error': 'Cannot read binary file; use /api/server-files/content for inline preview',
                'path': str(file_path),
            }, status=400)

        return web.json_response({
            'path': str(file_path.resolve()),
            'name': file_path.name,
            'size': file_path.stat().st_size,
            'content': content,
            'language': _server_file_language(file_path),
            'content_type': _server_file_content_type(file_path),
        })
    except web.HTTPBadRequest as exc:
        return web.json_response(json.loads(exc.text), status=400)
    except Exception as e:
        logger.error(f"Error reading server file: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_server_files_content(request: web.Request) -> web.Response:
    """Serve file bytes for inline browser preview."""
    try:
        file_path = _resolve_server_file_path(request.query.get('path'))
        logger.debug("[server-files] content path=%s", file_path)
        if not file_path.exists():
            return web.json_response({'error': 'File not found', 'path': str(file_path)}, status=404)
        if not file_path.is_file():
            return web.json_response({'error': 'Not a file', 'path': str(file_path)}, status=400)

        response = web.FileResponse(file_path)
        response.content_type = _server_file_content_type(file_path)
        response.headers['Content-Disposition'] = f'inline; filename="{file_path.name}"'
        return response
    except web.HTTPBadRequest as exc:
        return web.json_response(json.loads(exc.text), status=400)
    except Exception as e:
        logger.error(f"Error serving server file content: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_server_files_upload(request: web.Request) -> web.Response:
    """Upload files to workspace root (with optional ZIP extraction)."""
    try:
        reader = await request.multipart()
        target_path_raw: Optional[str] = None
        upload_filename: Optional[str] = None
        upload_content_type: Optional[str] = None
        upload_payload: Optional[bytes] = None

        while True:
            field = await reader.next()
            if field is None:
                break
            if field.name == 'path':
                target_path_raw = await field.text()
            elif field.name == 'file' and upload_payload is None:
                upload_filename = Path(field.filename or 'upload.bin').name
                upload_content_type = getattr(field, 'content_type', None)
                # Consume upload bytes immediately so multipart iteration cannot
                # drain/discard the file payload before we process it.
                upload_payload = await field.read(decode=False)

        if upload_payload is None:
            return web.json_response({'success': False, 'error': 'file is required'}, status=400)

        target_dir = _resolve_server_file_path(target_path_raw)
        if not target_dir.exists():
            return web.json_response({'success': False, 'error': 'Target path not found'}, status=404)
        if not target_dir.is_dir():
            return web.json_response({'success': False, 'error': 'Target path must be a directory'}, status=400)

        filename = upload_filename or 'upload.bin'
        logger.info("[server-files] upload target=%s filename=%s", target_dir, filename)
        payload = upload_payload

        if filename.lower().endswith('.zip'):
            payload_size = len(payload)
            starts_with_zip_signature = payload.startswith(b'PK')
            is_zip_payload = zipfile.is_zipfile(io.BytesIO(payload)) if payload else False
            logger.info(
                "[server-files] zip upload diagnostics target=%s filename=%s content_type=%s payload_size=%s starts_with_pk=%s is_zipfile=%s",
                target_dir,
                filename,
                upload_content_type,
                payload_size,
                starts_with_zip_signature,
                is_zip_payload,
            )

            if payload_size == 0:
                return web.json_response({'success': False, 'error': 'Uploaded ZIP file is empty'}, status=400)
            if not is_zip_payload:
                return web.json_response({'success': False, 'error': 'Uploaded file is not a valid ZIP archive'}, status=400)

            try:
                archive = zipfile.ZipFile(io.BytesIO(payload))
                items: List[str] = []
                extracted_count = 0
                with archive:
                    for member in archive.infolist():
                        member_path = PurePosixPath(member.filename)
                        if not member.filename or member.filename.endswith('/'):
                            continue
                        if member_path.is_absolute() or '..' in member_path.parts:
                            return web.json_response({'success': False, 'error': f'Unsafe ZIP entry: {member.filename}'}, status=400)

                        destination = (target_dir / Path(*member_path.parts)).resolve(strict=False)
                        if not _is_within_root(destination, target_dir):
                            return web.json_response({'success': False, 'error': f'Unsafe ZIP entry: {member.filename}'}, status=400)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member, 'r') as source, open(destination, 'wb') as sink:
                            shutil.copyfileobj(source, sink)
                        items.append(str(destination))
                        extracted_count += 1

                logger.info(
                    "[server-files] zip extract success target=%s filename=%s extracted_count=%s",
                    target_dir,
                    filename,
                    extracted_count,
                )
                return web.json_response({
                    'success': True,
                    'mode': 'zip_extract',
                    'target_path': str(target_dir),
                    'uploaded_filename': filename,
                    'extracted_count': extracted_count,
                    'items': items,
                })
            except zipfile.BadZipFile:
                return web.json_response({'success': False, 'error': 'Uploaded ZIP archive is malformed'}, status=400)
            except RuntimeError as exc:
                return web.json_response({'success': False, 'error': 'Failed to extract ZIP archive', 'detail': str(exc)}, status=400)
            except ValueError as exc:
                return web.json_response({'success': False, 'error': 'Invalid ZIP archive parameters', 'detail': str(exc)}, status=400)
            except NotImplementedError as exc:
                return web.json_response({'success': False, 'error': 'ZIP archive uses an unsupported feature', 'detail': str(exc)}, status=400)

        destination = (target_dir / filename).resolve(strict=False)
        if not _is_within_root(destination, target_dir):
            return web.json_response({'success': False, 'error': 'Invalid target filename'}, status=400)
        # Intentionally allow explicit zero-byte regular file uploads.
        with open(destination, 'wb') as output:
            output.write(payload)

        return web.json_response({
            'success': True,
            'mode': 'file_save',
            'target_path': str(target_dir),
            'uploaded_filename': filename,
            'saved_path': str(destination),
        })
    except web.HTTPBadRequest as exc:
        return web.json_response({'success': False, **json.loads(exc.text)}, status=400)
    except Exception as e:
        logger.error(f"Error uploading server file: {e}")
        return web.json_response({'success': False, 'error': str(e)}, status=500)


async def api_server_files_delete(request: web.Request) -> web.Response:
    """Delete one or more files/directories rooted under workspace."""
    try:
        body = await request.json()
        raw_paths = body.get('paths')
        if not isinstance(raw_paths, list) or len(raw_paths) == 0:
            return web.json_response({'success': False, 'error': 'paths is required and must be a non-empty list'}, status=400)
        if not all(isinstance(raw, str) for raw in raw_paths):
            return web.json_response({'success': False, 'error': 'paths must be a list of strings'}, status=400)

        root = _workspace_root()
        resolved_paths = [_resolve_server_file_path(raw) for raw in raw_paths]
        for resolved in resolved_paths:
            if resolved == root:
                return web.json_response({'success': False, 'error': 'Deleting workspace root is not allowed'}, status=400)

        deleted = []
        for resolved in resolved_paths:
            if not resolved.exists() and not resolved.is_symlink():
                return web.json_response({'success': False, 'error': 'Path not found', 'path': str(resolved)}, status=404)

            if resolved.is_symlink() or resolved.is_file():
                resolved.unlink()
                deleted.append({'path': str(resolved), 'type': 'file'})
            elif resolved.is_dir():
                shutil.rmtree(resolved)
                deleted.append({'path': str(resolved), 'type': 'directory'})
            else:
                return web.json_response({'success': False, 'error': 'Unsupported path type', 'path': str(resolved)}, status=400)

        logger.info("[server-files] delete count=%s", len(deleted))
        return web.json_response({'success': True, 'deleted': deleted})
    except web.HTTPBadRequest as exc:
        return web.json_response({'success': False, **json.loads(exc.text)}, status=400)
    except json.JSONDecodeError:
        return web.json_response({'success': False, 'error': 'Invalid JSON body'}, status=400)
    except Exception as e:
        logger.error(f"Error deleting server files: {e}")
        return web.json_response({'success': False, 'error': str(e)}, status=500)


async def api_server_files_download(request: web.Request) -> web.Response:
    """Download one or more files/directories rooted under workspace."""
    try:
        raw_paths = list(request.query.getall('paths', []))
        if not raw_paths and request.query.get('path'):
            raw_paths.append(request.query.get('path', ''))
        if not raw_paths:
            return web.json_response({'success': False, 'error': 'path is required'}, status=400)

        resolved_paths = [_resolve_server_file_path(raw) for raw in raw_paths if raw is not None]
        for resolved in resolved_paths:
            if not resolved.exists():
                return web.json_response({'success': False, 'error': 'File not found', 'path': str(resolved)}, status=404)

        logger.debug("[server-files] download paths=%s", ",".join(str(path) for path in resolved_paths))
        if len(resolved_paths) == 1 and resolved_paths[0].is_file():
            file_path = resolved_paths[0]
            response = web.FileResponse(file_path)
            response.content_type = _server_file_content_type(file_path)
            response.headers['Content-Disposition'] = f'attachment; filename="{file_path.name}"'
            return response

        archive = await asyncio.to_thread(_create_server_files_zip, resolved_paths)
        archive_name = f"{resolved_paths[0].name}.zip" if len(resolved_paths) == 1 else "files.zip"
        return web.Response(
            body=archive,
            content_type='application/zip',
            headers={'Content-Disposition': f'attachment; filename="{archive_name}"'},
        )
    except web.HTTPBadRequest as exc:
        return web.json_response({'success': False, **json.loads(exc.text)}, status=400)
    except Exception as e:
        logger.error(f"Error downloading server files: {e}")
        return web.json_response({'success': False, 'error': str(e)}, status=500)


async def api_browse_files(request: web.Request) -> web.Response:
    """Backward-compatible alias for legacy file browsing endpoint."""
    return await api_server_files_browse(request)


async def api_read_file(request: web.Request) -> web.Response:
    """Backward-compatible alias for legacy text file reader endpoint."""
    return await api_server_files_read(request)


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


async def api_rename_session(request: web.Request) -> web.Response:
    """Rename an existing session.

    POST /api/sessions/{session_id}/rename
    Body: {"name": "new title"}
    """
    try:
        if not session_manager._initialized:
            await session_manager.initialize()

        session_id = request.match_info.get('session_id', '')
        if not session_id:
            return web.json_response({'error': 'Session ID required'}, status=400)

        try:
            data = await request.json()
        except (json.JSONDecodeError, ContentTypeError):
            return web.json_response({'error': 'Invalid JSON in request body'}, status=400)

        name = data.get('name')
        try:
            renamed = await session_manager.rename_session(session_id, name)
        except ValueError as exc:
            return web.json_response({'error': str(exc)}, status=400)

        if renamed is None:
            return web.json_response({'error': 'Session not found'}, status=404)

        return web.json_response({'success': True, 'session_id': session_id, 'name': renamed})
    except Exception as e:
        logger.error(f"Error renaming session: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_delete_session(request: web.Request) -> web.Response:
    """Delete an existing session.

    DELETE /api/sessions/{session_id}
    """
    try:
        if not session_manager._initialized:
            await session_manager.initialize()

        session_id = request.match_info.get('session_id', '')
        if not session_id:
            return web.json_response({'error': 'Session ID required'}, status=400)

        deleted = await session_manager.delete_session(session_id)
        if not deleted:
            return web.json_response({'error': 'Session not found'}, status=404)

        return web.json_response({'success': True, 'session_id': session_id})
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
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


def _remove_legacy_ssh_config(config_data: Dict[str, Any]) -> None:
    """Remove deprecated top-level SSH configuration."""
    if isinstance(config_data, dict):
        config_data.pop("ssh", None)


async def api_save_config(request: web.Request) -> web.Response:
    """Save configuration to config.yaml.
    
    POST /api/config/save
    Body: JSON with config sections to update (partial updates supported)
    
    This endpoint performs partial saves - only modified fields are updated,
    preserving existing values for unchanged fields within each section.
    """
    try:
        data = await request.json()
        sections = ['llm', 'jira', 'confluence', 'github', 'git', 'debug', 'proxy']
        payload = dict(data) if isinstance(data, dict) else {}
        _remove_legacy_ssh_config(payload)
        updated_sections = global_config.save_partial_sections(payload, sections)
        
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
        effective_config = global_config.get_effective_config()
        _remove_legacy_ssh_config(effective_config)
        return web.json_response(
            {
                'config': effective_config,
                'runtime_profile': global_config.get_managed_overlay_meta(),
            }
        )
    except Exception as e:
        logger.error(f"Error reading config: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_apply_runtime_profile(request: web.Request) -> web.Response:
    """Apply managed runtime-profile snapshot from trusted Portal control-plane request."""
    if not _is_trusted_portal_request(request):
        return web.json_response({"error": "Forbidden"}, status=403)

    try:
        data = await request.json()
        runtime_profile_id = data.get("runtime_profile_id")
        revision = data.get("revision")
        overlay_config = data.get("config") if isinstance(data.get("config"), dict) else {}

        if runtime_profile_id is None and revision is None and not overlay_config:
            global_config.clear_managed_overlay()
            return web.json_response(
                {
                    "success": True,
                    "runtime_profile_id": None,
                    "revision": None,
                    "updated_sections": [],
                    "cleared": True,
                }
            )

        updated_sections = global_config.set_managed_overlay(runtime_profile_id, revision, overlay_config)
        return web.json_response(
            {
                "success": True,
                "runtime_profile_id": runtime_profile_id,
                "revision": revision,
                "updated_sections": updated_sections,
                "cleared": False,
            }
        )
    except Exception as e:
        logger.error("Error applying runtime profile config: %s", e, exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)


# ========== GitHub Copilot Authorization ==========

import httpx
import uuid

# In-memory storage for pending authorizations (in production, use Redis/database)
_pending_authorizations: Dict[str, Dict[str, Any]] = {}

def _get_github_api_base_url() -> str:
    """Return normalized GitHub API base URL from github.base_url config."""
    return normalize_github_api_base_url(global_config.get("github.base_url"))


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
        # Get normalized GitHub API base URL from config
        api_base_url = _get_github_api_base_url()
        
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
        
        # Get normalized GitHub API base URL from config
        api_base_url = _get_github_api_base_url()
        
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
    """Backward-compatible alias for legacy file download endpoint."""
    return await api_server_files_download(request)


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
        POST /api/tasks/execute - Accept and execute structured runtime task
        GET  /api/tasks/{task_id} - Runtime task status for portal polling
        GET  /api/sessions - List recent sessions
        GET  /api/sessions/{session_id} - Load session messages
        POST /api/sessions/{session_id}/rename - Rename existing session
        DELETE /api/sessions/{session_id} - Delete existing session
        GET  /api/server-files/* - Canonical path-based workspace file API
        GET  /api/files and /api/files/read - Legacy path-based compatibility aliases
        /api/files/{file_id}* - File-ID attachment APIs (separate from path-based server files)
        GET  /api/usage   - Get usage stats
        POST /api/clear   - Clear session
        GET  /api/skills  - Get available skills
    """
    app.router.add_get('/', serve_webchat)
    
    app.router.add_get('/static/{path:.*}', serve_static)
    app.router.add_post('/api/chat', api_chat)
    app.router.add_post('/api/chat/stream', api_chat_stream)
    app.router.add_post('/api/tasks/execute', api_tasks_execute)
    app.router.add_get('/api/tasks/{task_id}', api_task_status)
    app.router.add_get('/api/capabilities', api_capabilities)
    app.router.add_get('/api/sessions', api_sessions)
    app.router.add_get('/api/sessions/{session_id}', api_load_session)
    app.router.add_post('/api/sessions/{session_id}/rename', api_rename_session)
    app.router.add_delete('/api/sessions/{session_id}', api_delete_session)
    app.router.add_get('/api/sessions/{session_id}/chatlog', api_session_chatlog)
    # Primary workspace path-based API
    app.router.add_get('/api/server-files', api_server_files_browse)
    app.router.add_get('/api/server-files/read', api_server_files_read)
    app.router.add_get('/api/server-files/content', api_server_files_content)
    app.router.add_post('/api/server-files/upload', api_server_files_upload)
    app.router.add_post('/api/server-files/delete', api_server_files_delete)
    app.router.add_get('/api/server-files/download', api_server_files_download)

    # Legacy compatibility aliases for older clients (path-based)
    app.router.add_get('/api/files', api_browse_files)
    app.router.add_get('/api/files/read', api_read_file)
    app.router.add_get('/api/usage', api_usage)
    app.router.add_post('/api/clear', api_clear)
    app.router.add_post('/api/sessions/{session_id}/messages/{message_id}/edit', api_edit_message)
    app.router.add_post('/api/sessions/{session_id}/messages/{message_id}/delete-from-here', api_delete_conversation_from)
    app.router.add_get('/api/config', api_get_config)
    app.router.add_post('/api/config/save', api_save_config)
    app.router.add_post('/api/internal/runtime-profile/apply', api_apply_runtime_profile)
    app.router.add_get('/api/skills', api_skills)
    app.router.add_post('/api/copilot/auth/start', api_copilot_auth_start)
    app.router.add_post('/api/copilot/auth/check', api_copilot_auth_check)
    
    # Attachment APIs (file-id based, separate from path-based server-files routes)
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
    logger.info("  POST /api/tasks/execute - Accept and execute structured runtime task")
    logger.info("  GET  /api/tasks/{task_id} - Runtime task status for portal polling")
    logger.info("  GET  /api/sessions - List recent sessions")
    logger.info("  GET  /api/sessions/{id} - Load session messages")
    logger.info("  POST /api/sessions/{id}/rename - Rename existing session")
    logger.info("  DELETE /api/sessions/{id} - Delete existing session")
    logger.info("  GET  /api/server-files - Browse workspace files")
    logger.info("  GET  /api/server-files/read - Read text file content")
    logger.info("  GET  /api/server-files/content - Inline file content")
    logger.info("  POST /api/server-files/upload - Upload/extract files into workspace")
    logger.info("  POST /api/server-files/delete - Delete files/directories from workspace")
    logger.info("  GET  /api/server-files/download - Download file(s) from workspace")
    logger.info("  GET  /api/files (legacy alias) - Compatibility browse route")
    logger.info("  GET  /api/files/read (legacy alias) - Compatibility text-read route")
    logger.info("  /api/files/{file_id}* - Attachment APIs (file-id based, separate namespace)")
    logger.info("  GET  /api/usage   - Get usage stats")
    logger.info("  POST /api/clear   - Clear session")
    logger.info("  GET  /api/skills  - Get available skills")
