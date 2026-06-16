"""API-only runtime gateway routes for Engineering Flow Platform."""

import asyncio
import hashlib
import json
import logging
import os
import re
import uuid
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from aiohttp import web, ContentTypeError
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.file_parser.storage import init_storage, StoredFileNotFoundError, get_metadata
init_storage()
from src.utils.file_parser import parse_file
from src.utils.truncate import truncate
from src.utils.redaction import safe_preview, safe_log_field, sanitize_exception_message
from src.utils.logger import clear_log_context, set_log_context


from src.agents.errors import extract_error_details, LLMError
from src.hooks.file_context import inject_context
from src.hooks.file_context.models import Chunk, SessionFileMeta
from src.hooks.file_context.retrieval import retrieval_engine
from src.hooks.file_context.storage import storage as file_context_storage
from src.config import config as global_config, DEFAULT_LLM_MODEL
from src.runtime.chat_orchestration_adapter import execute_runtime_task_request
from src.runtime.runtime_task_tracker import RuntimeTaskTracker
from src.runtime.runtime_task_session_coordinator import RuntimeTaskSessionCoordinator
from src.runtime.portal_session_metadata_client import (
    extract_session_metadata_publish_fields,
    publish_session_metadata,
)
from src.runtime.progressive_context import build_portal_context_preview
from src.gateway.chat_payloads import (
    build_runtime_response_payload,
    normalize_assistant_history_message,
)
from src.gateway.runtime_chat import (
    RuntimeChatError,
    run_runtime_chat,
)
from src.gateway.runtime_event_projection import (
    RuntimeEventProjector,
    is_projected_runtime_event,
)
from src.gateway.runtime_request_contracts import (
    build_stream_start_event_payload,
    extract_trusted_client_request_id,
)
from src.runtime.capability_registry import get_capability_registry
from src.gateway.event_bus import emit_agent_event
from src.efp_runtime.session.gateway_facade import (
    RuntimeSessionArtifacts,
    resolve_session_display_name,
    runtime_session_manager as session_manager,
)
from src.sessions.usage import usage_tracker
from src.workspace_defaults import resolve_runtime_workspace

logger = logging.getLogger(__name__)
runtime_task_tracker = RuntimeTaskTracker()
runtime_task_session_coordinator = RuntimeTaskSessionCoordinator()
runtime_session_artifacts = RuntimeSessionArtifacts()


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


async def _enrich_publish_metadata_with_context_preview(
    metadata: Optional[Dict[str, Any]],
    *,
    session_id: Optional[str],
) -> Dict[str, Any]:
    merged = dict(metadata or {})
    if not session_id:
        return merged
    context_state = await session_manager.get_context_state(session_id)
    preview = build_portal_context_preview(context_state)
    if preview:
        merged.update(preview)
    active_skill = await session_manager.get_active_skill_session(session_id)
    if isinstance(active_skill, dict):
        merged.update(
            {
                "active_skill_name": active_skill.get("skill_name") or active_skill.get("skill"),
                "active_skill_status": active_skill.get("status"),
                "active_skill_goal": active_skill.get("goal"),
                "active_skill_hash": active_skill.get("skill_hash"),
                "active_skill_turn_count": active_skill.get("turn_count"),
                "active_skill_activation_reason": active_skill.get("activation_reason"),
                "active_skill_tool_policy_declared": active_skill.get("tool_policy_declared"),
            }
        )
    return merged


def _is_trusted_portal_request(request: web.Request) -> bool:
    headers = getattr(request, "headers", {}) or {}
    portal_source = str(headers.get("X-Portal-Author-Source") or "").strip().lower()
    return portal_source == "portal"


def _resolve_chat_display_user_name(data: Dict[str, Any], portal_user_name: Optional[str]) -> str:
    direct_user_name = _sanitize_portal_identity_value(data.get("user_name")) or None
    return portal_user_name or direct_user_name or "runtime-api-user"


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


def _resolve_runtime_session_id(data: Dict[str, Any]) -> str:
    # Keep explicit client IDs (trimmed), but generate collision-safe defaults.
    # The UUID suffix avoids same-second timestamp collisions across rapid new sessions.
    candidate = data.get("session_id")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return f"runtime_api_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


async def _persist_chat_failure_state(
    *,
    agent_id: Optional[str],
    session_id: Optional[str],
    request_id: Optional[str],
    user_message: str,
    error_type: str,
    metadata: Optional[Dict[str, Any]],
) -> None:
    # Best-effort only: persistence/metadata failures must never mask the original request failure.
    # Persisted system errors are excluded from model context so retry turns are not polluted.
    if not session_id:
        return

    try:
        if not session_manager._initialized:
            await session_manager.initialize()

        summary = user_message.strip() if isinstance(user_message, str) else ""
        content = f"System error: {summary}" if summary else "System error: request failed."
        await session_manager.add_message(
            session_id,
            "assistant",
            content,
            wait_for_save=True,
            extra={
                "author_name": "System",
                "author_source": "system",
                "exclude_from_model_context": True,
                "metadata": {
                    "kind": "system_error",
                    "request_id": request_id,
                    "error_type": error_type,
                    "exclude_from_model_context": True,
                    "ui_hint": "system_error",
                    **({"display_message": summary} if summary else {}),
                },
            },
        )

        if agent_id and session_id:
            await publish_session_metadata(
                agent_id=agent_id,
                session_id=session_id,
                last_execution_id=request_id,
                latest_event_type="chat.failed",
                latest_event_state="error",
                snapshot_version=None,
                runtime_events=[
                    {
                        "event_type": "execution.failed",
                        "state": "error",
                        "request_id": request_id,
                        "session_id": session_id,
                        "summary": summary,
                        "detail_payload": {
                            "message": summary,
                            "error_type": error_type,
                        },
                        "created_at": datetime.utcnow().isoformat() + "Z",
                    }
                ],
                metadata=metadata or {},
            )
    except Exception:
        logger.warning("Best-effort chat failure persistence failed", exc_info=True)


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


def _resolve_runtime_task_session_id(
    *,
    task_id: str,
    task_type: str,
    session_id: Optional[str],
    input_payload: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Optional[str]:
    """Return the durable Native task session id for task-style execution."""
    for value in (
        input_payload.get("task_session_id"),
        metadata.get("portal_task_session_id"),
        metadata.get("runtime_task_session_id"),
        input_payload.get("_runtime_session_id"),
        metadata.get("task_session_id"),
    ):
        if isinstance(value, str) and value.strip():
            return _safe_runtime_task_session_id(value.strip())
    if session_id:
        return _safe_runtime_task_session_id(session_id)
    if task_type == "agent_async_task" and task_id:
        return _safe_runtime_task_session_id(f"agent-task-{task_id}")
    if task_type == "generic_agent_task" and task_id:
        return _safe_runtime_task_session_id(f"generic-task-{task_id}")
    return None


def _safe_runtime_task_session_id(value: str) -> str:
    """Keep task-backed chat session ids portable across file-backed stores."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return cleaned or f"task-session-{hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:16]}"


def _resolve_runtime_task_delivery(input_payload: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    for value in (
        input_payload.get("delivery"),
        input_payload.get("runtime_task_delivery"),
        metadata.get("delivery"),
        metadata.get("runtime_task_delivery"),
    ):
        if isinstance(value, str) and value.strip().lower() == "queue":
            return "queue"
        if isinstance(value, str) and value.strip().lower() == "steer":
            return "steer"
    return "steer"


def _sanitize_trace_value(value: Any, max_len: int = 128) -> Optional[str]:
    if value is None:
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", str(value)).strip()
    if not cleaned:
        return None
    return cleaned[:max_len]




def _normalize_attachment_ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    seen: Set[str] = set()
    for item in value:
        if isinstance(item, str):
            file_id = item.strip()
        elif isinstance(item, dict):
            file_id = str(item.get("file_id") or item.get("fileId") or item.get("id") or "").strip()
        else:
            continue
        if file_id and file_id not in seen:
            result.append(file_id)
            seen.add(file_id)
    return result


async def _cleanup_one_shot_attachments(session_id: Optional[str], file_ids: List[str]) -> None:
    if not session_id or not file_ids:
        return
    unique_ids: List[str] = []
    seen: Set[str] = set()
    for fid in file_ids:
        if fid and fid not in seen:
            unique_ids.append(fid)
            seen.add(fid)

    from src.utils.file_parser.storage import delete_file
    from src.hooks.file_context.storage import storage as file_context_storage
    from src.hooks.file_context.retrieval import retrieval_engine

    touched_context = False
    for fid in unique_ids:
        try:
            if hasattr(file_context_storage, "remove_file_from_session"):
                touched_context = file_context_storage.remove_file_from_session(session_id, fid) or touched_context
            else:
                file_context_storage.delete_file_chunks(fid)
                touched_context = True
        except Exception:
            logger.warning("Best-effort file_context cleanup failed for %s/%s", session_id, fid, exc_info=True)

        try:
            delete_file(fid)
        except Exception:
            logger.warning("Best-effort upload cleanup failed for %s", fid, exc_info=True)

    if touched_context:
        try:
            retrieval_engine.rebuild_index(session_id)
        except Exception:
            logger.warning("Best-effort retrieval index rebuild failed for %s", session_id, exc_info=True)


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

    # One-shot mode: only explicit request attachments are eligible.
    if attachments and isinstance(attachments, list):
        for file_id in attachments:
            await process_file(file_id)

    return attached_images


def _chunk_context_text(chunk: Chunk) -> str:
    return (chunk.content or chunk.markdown or chunk.table_json or "").strip()


async def _parse_file_into_file_context(*, session_id: str, file_id: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metadata = get_metadata(file_id)
    if metadata.session_id and metadata.session_id != session_id:
        return {"success": False, "error": "File not found", "file_id": file_id}
    if not file_context_storage.get_file_meta(session_id, file_id):
        file_context_storage.add_file_to_session(session_id, SessionFileMeta(
            file_id=file_id, session_id=session_id, filename=metadata.original_filename, content_type=metadata.content_type, parse_status="pending"
        ))
    file_context_storage.update_file_status(session_id, file_id, status="processing")
    result = await parse_file(file_id, options or {})
    if not result.success:
        file_context_storage.update_file_status(session_id, file_id, status="failed", error=result.error)
        return {"success": False, "error": result.error, "file_id": file_id, "result": result}

    new_chunks: List[Chunk] = []
    total_chars = 0
    for block in (result.blocks or []):
        block_data = block.model_dump(by_alias=True, exclude_none=True)
        content = (block_data.get("content") or "").strip()
        if not content:
            content = (block_data.get("markdown") or "").strip()
        if not content and block_data.get("json") is not None:
            content = json.dumps(block_data.get("json"), ensure_ascii=False)
        if not content and block_data.get("table_json") is not None:
            content = json.dumps(block_data.get("table_json"), ensure_ascii=False)
        if not content:
            continue
        chunk_index = len(new_chunks) + 1
        chunk_id = block_data.get("chunk_id") or f"{file_id}_{block_data.get('type', 'chunk')}_{block_data.get('page', 1)}_{block_data.get('index', chunk_index)}"
        table_data = block_data.get("json") if block_data.get("json") is not None else block_data.get("table_json")
        chunk = Chunk(
            chunk_id=chunk_id, file_id=file_id, session_id=session_id, type=block_data.get("type", "paragraph"),
            content=content, markdown=block_data.get("markdown"), page=block_data.get("page"), index=block_data.get("index", chunk_index),
            row_range=block_data.get("row_range"), source=block_data.get("method", "unknown"), confidence=block_data.get("confidence", 0.95),
            content_hash=hashlib.sha256(content.encode()).hexdigest(), bbox=block_data.get("bbox"),
            table_json=json.dumps(table_data, ensure_ascii=False) if table_data is not None else None,
        )
        new_chunks.append(chunk)
        total_chars += len(content)
    if not new_chunks and result.markdown:
        fallback_content = str(result.markdown).strip()
        if fallback_content:
            new_chunks.append(Chunk(
                chunk_id=f"{file_id}_markdown_1_1", file_id=file_id, session_id=session_id, type="paragraph", content=fallback_content,
                markdown=fallback_content, source="fallback", confidence=0.95, content_hash=hashlib.sha256(fallback_content.encode()).hexdigest(),
            ))
            total_chars = len(fallback_content)
    if not new_chunks:
        error = "Parsed file did not produce any text chunks"
        file_context_storage.update_file_status(session_id, file_id, status="failed", error=error)
        return {"success": False, "error": error, "file_id": file_id, "saved_chunks": 0, "total_chars": 0}

    file_context_storage.delete_file_chunks(file_id)
    for chunk in new_chunks:
        file_context_storage.save_chunk(chunk)
    file_context_storage.update_file_status(session_id=session_id, file_id=file_id, status="completed", chunk_count=len(new_chunks), total_chars=total_chars)
    retrieval_engine.rebuild_index(session_id)
    return {"success": True, "result": result, "saved_chunks": len(new_chunks), "total_chars": total_chars}


async def _ensure_chat_attachment_context(*, session_id: str, attachment_ids: List[str]) -> Dict[str, Any]:
    context_file_ids: List[str] = []
    image_file_ids: List[str] = []
    failures: List[Dict[str, str]] = []
    parsed_file_ids: List[str] = []
    already_ready_file_ids: List[str] = []
    for file_id in list(dict.fromkeys(attachment_ids)):
        try:
            metadata = get_metadata(file_id)
        except Exception as e:
            failures.append({"file_id": file_id, "error": str(e)})
            continue
        if metadata.session_id and metadata.session_id != session_id:
            failures.append({"file_id": file_id, "error": "File not found"})
            continue
        if (metadata.content_type or "").startswith("image/"):
            image_file_ids.append(file_id)
            continue
        file_meta = file_context_storage.get_file_meta(session_id, file_id)
        if not file_meta:
            file_context_storage.add_file_to_session(session_id, SessionFileMeta(
                file_id=file_id, session_id=session_id, filename=metadata.original_filename, content_type=metadata.content_type, parse_status="pending"
            ))
            file_meta = file_context_storage.get_file_meta(session_id, file_id)
        chunks = file_context_storage.get_file_chunks(file_id)
        has_text_chunks = any(_chunk_context_text(c) for c in chunks)
        if file_meta and file_meta.parse_status == "completed" and has_text_chunks:
            context_file_ids.append(file_id)
            already_ready_file_ids.append(file_id)
            continue
        parse_res = await _parse_file_into_file_context(session_id=session_id, file_id=file_id)
        if parse_res.get("success"):
            reparsed_chunks = file_context_storage.get_file_chunks(file_id)
            if any(_chunk_context_text(c) for c in reparsed_chunks):
                context_file_ids.append(file_id)
                parsed_file_ids.append(file_id)
            else:
                failures.append({"file_id": file_id, "error": "Parsed file did not produce any text chunks"})
        else:
            failures.append({"file_id": file_id, "error": str(parse_res.get("error") or "Parse failed")})
    return {"context_file_ids": context_file_ids, "image_file_ids": image_file_ids, "failures": failures, "parsed_file_ids": parsed_file_ids, "already_ready_file_ids": already_ready_file_ids}


def _build_direct_attachment_context_prompt(*, session_id: str, file_ids: List[str], user_question: str, max_chars: int = 12000) -> Optional[str]:
    parts: List[str] = []
    used = 0
    has_chunk_text = False
    for file_id in file_ids:
        meta = file_context_storage.get_file_meta(session_id, file_id)
        header = f"--- File: {(meta.filename if meta else file_id)} ({(meta.content_type if meta else 'unknown')}) ---"
        if used + len(header) > max_chars:
            break
        parts.append(header)
        used += len(header) + 1
        for idx, chunk in enumerate(file_context_storage.get_file_chunks(file_id), 1):
            text = _chunk_context_text(chunk)
            if not text:
                continue
            chunk_header = f"--- Chunk {idx}{(', rows ' + chunk.row_range) if chunk.row_range else ''} ---"
            candidate = f"{chunk_header}\n{text}\n"
            if used + len(candidate) > max_chars:
                remain = max_chars - used
                if remain > 64:
                    parts.append(candidate[:remain])
                    has_chunk_text = True
                used = max_chars
                break
            parts.append(candidate)
            used += len(candidate)
            has_chunk_text = True
        if used >= max_chars:
            break
    if not has_chunk_text:
        return None
    return f"Based on the attached file context, answer the user's request.\n\nUser request:\n{user_question}\n\nAttached file context:\n" + "\n".join(parts) + "\n\nAnswer:"


def _prepare_attachment_transient_model_message(
    *,
    session_id: str,
    context_file_ids: List[str],
    model_context_query: str,
    top_k: int = 5,
    max_tokens: int = 4000,
) -> Tuple[Optional[str], List[Dict[str, Any]], str]:
    transient_model_message: Optional[str] = None
    citations: List[Dict[str, Any]] = []
    source = "none"
    try:
        enhanced_message, _budget_status, citations = inject_context(
            session_id=session_id,
            message=model_context_query,
            top_k=top_k,
            max_tokens=max_tokens,
            file_ids=context_file_ids,
        )
        if enhanced_message and enhanced_message != model_context_query:
            transient_model_message = enhanced_message
            source = "inject_context"
    except Exception:
        logger.warning("[attachment_context] Failed to inject file context", exc_info=True)

    if not transient_model_message:
        transient_model_message = _build_direct_attachment_context_prompt(
            session_id=session_id, file_ids=context_file_ids, user_question=model_context_query
        )
        if transient_model_message:
            source = "direct_fallback"
    return transient_model_message, citations, source


def _build_attachment_parse_failure_notice(failures: List[Dict[str, Any]]) -> str:
    if not failures:
        return ""
    lines = ["Some attached file(s) could not be parsed and are not available to the model:"]
    for item in failures:
        file_id = str(item.get("file_id") or "unknown")
        error = safe_preview(str(item.get("error") or "unknown error"), 240)
        lines.append(f"- {file_id}: {error}")
    lines.append("")
    lines.append("Do not claim to have read or summarized those failed attachment(s). Answer only from the available text context, image attachment(s), and the user's message.")
    return "\n".join(lines)


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




def _sse_event_bytes(event_name: str, payload: Any) -> bytes:
    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
    ).encode("utf-8")


def _json_compatible(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


async def _run_chat_via_execution_bus(
    *,
    session_id: str,
    message: str,
    user_name: str,
    portal_user_id: Optional[str],
    portal_user_name: Optional[str],
    attached_images: Optional[List[str]] = None,
    attachments: Optional[List[str]] = None,
    transient_model_message: Optional[str] = None,
    reasoning_replay: Optional[bool] = None,
    stream_callback: Optional[Any] = None,
    request_path: str = "/api/chat",
    execution_metadata: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    request_id: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    resolved_request_id = request_id or f"chat-{uuid.uuid4()}"
    return await run_runtime_chat(
        request_id=resolved_request_id,
        session_id=session_id,
        message=message,
        user_name=user_name,
        portal_user_id=portal_user_id,
        portal_user_name=portal_user_name,
        attached_images=attached_images,
        attachments=attachments,
        transient_model_message=transient_model_message,
        reasoning_replay=reasoning_replay,
        stream_callback=stream_callback,
        request_path=request_path,
        execution_metadata=execution_metadata,
        agent_id=agent_id,
        agent_name=agent_name,
        model=model,
    )


async def _emit_gateway_runtime_event(event_payload: Dict[str, Any]) -> None:
    """Best-effort bridge from chat SSE events to the gateway WebSocket bus."""

    try:
        maybe_result = emit_agent_event(
            str(event_payload.get("type") or event_payload.get("event_type") or "runtime_event"),
            event_payload,
        )
        if asyncio.iscoroutine(maybe_result):
            await maybe_result
    except Exception:
        logger.debug("Best-effort gateway runtime event emit failed", exc_info=True)


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


async def api_chat(request: web.Request) -> web.Response:
    """Handle chat API requests.
    
    POST /api/chat
    Body: {"message": "...", "session_id": "optional", "attachments": ["file_id1", "file_id2"], "reasoning_replay": false}
    """
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    runtime_agent_id: Optional[str] = None
    execution_metadata: Dict[str, Any] = {}
    attachment_ids: List[str] = []
    clear_log_context()
    trace_headers = _extract_task_trace_headers(request)
    set_log_context(
        trace_id=trace_headers.get("trace_id"),
        span_id=trace_headers.get("span_id"),
        parent_span_id=trace_headers.get("parent_span_id"),
        path="/api/chat",
        runtime_type=os.getenv("EFP_RUNTIME_TYPE", "native"),
        execution_type="chat",
        source_type="runtime_api",
    )
    try:
        data = await request.json()
        original_user_text = (data.get('message') or '').strip()
        message = original_user_text
        
        # Dynamic session_id with collision-safe default for multi-session support
        session_id = _resolve_runtime_session_id(data)
        
        # Get reasoning_replay setting
        reasoning_replay = data.get('reasoning_replay', None)
        
        # Get attachments from new field
        attachment_ids = _normalize_attachment_ids(data.get("attachments", []))
        portal_user_id, portal_user_name = _extract_portal_identity(request, data)
        execution_metadata = _extract_trusted_control_plane_metadata(request, data)
        client_request_id = _extract_trusted_client_request_id(request, data)
        request_id = client_request_id or f"chat-{uuid.uuid4()}"
        set_log_context(
            request_id=request_id,
            session_id=session_id,
        )
        effective_user_name = _resolve_chat_display_user_name(data, portal_user_name)
        logger.debug(
            "[api_chat] Request summary: session_id=%s, has_message=%s, attachment_count=%d, portal_user_id_present=%s",
            session_id,
            bool(message),
            len(attachment_ids),
            bool(portal_user_id),
        )
        
        if not message and not attachment_ids:
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
            attachments=attachment_ids,
        )
        
        attachment_context = await _ensure_chat_attachment_context(session_id=session_id, attachment_ids=attachment_ids) if attachment_ids else {"context_file_ids": [], "failures": []}
        context_file_ids = attachment_context.get("context_file_ids", [])
        failures = attachment_context.get("failures", [])
        failure_notice = _build_attachment_parse_failure_notice(failures)
        if failures:
            execution_metadata["attachment_parse_failures"] = failures
        if failures and not context_file_ids and not attached_images:
            return web.json_response({"error": "attachment_parse_failed", "message": "One or more attached files could not be parsed.", "failures": failures, "session_id": session_id, "request_id": request_id}, status=400)

        if original_user_text:
            history_message = original_user_text
        elif context_file_ids:
            history_message = "[attachment]"
        elif attached_images:
            history_message = "[image]"
        else:
            history_message = ""

        model_context_query = original_user_text or ("Please summarize the attached file(s)." if context_file_ids else history_message)

        if not history_message.strip() and not attached_images and not attachment_ids:
            return web.json_response({'error': 'Empty message'}, status=400)

        # Inject file context if user has uploaded files
        original_msg_for_history = history_message if history_message.strip() else ("[image]" if attached_images else "")
        logger.info("[api_chat] Message summary: session_id=%s attached_images=%d message_length=%d preview=%s", safe_log_field(session_id, 120), len(attached_images) if attached_images else 0, len(original_msg_for_history), safe_preview(original_msg_for_history, 120))
        transient_model_message: Optional[str] = None
        citations: List[Dict[str, Any]] = []
        if context_file_ids:
            transient_model_message, citations, _source = _prepare_attachment_transient_model_message(
                session_id=session_id,
                context_file_ids=context_file_ids,
                model_context_query=model_context_query,
                top_k=5,
                max_tokens=4000,
            )
            request['file_citations'] = citations
            if not transient_model_message:
                return web.json_response(
                    {
                        "error": "attachment_context_unavailable",
                        "message": "Attached file context could not be prepared for the model.",
                        "session_id": session_id,
                        "request_id": request_id,
                        "attachment_ids": context_file_ids,
                    },
                    status=400,
                )
            if failure_notice:
                transient_model_message = f"{transient_model_message}\n\n{failure_notice}"
        elif attached_images and failure_notice:
            transient_model_message = (
                f"{model_context_query or history_message or 'Please answer using the available image attachment(s).'}\n\n"
                f"{failure_notice}"
            )
        # Revalidate message is not empty to prevent downstream LLM input from being empty
        if not history_message.strip() and not transient_model_message:
            logger.error(f"[api_chat] ERROR: Final message is empty before Copilot API call. Payload: {json.dumps(data, ensure_ascii=False)}")
            return web.json_response({'error': 'Input field missing for Copilot API.'}, status=400)
        
        # Initialize session manager if needed
        if not session_manager._initialized:
            await session_manager.initialize()
        
        # Get request-scoped model (trusted portal override only)
        model_override = _extract_trusted_model_override(request, data)
        model = model_override or global_config.llm.get('model', DEFAULT_LLM_MODEL)
        
        # Run EFP runtime; session_manager remains the gateway-side history mirror.
        runtime_agent_id, runtime_agent_name = _resolve_runtime_agent_identity(request)
        set_log_context(agent_id=runtime_agent_id)
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
                message=history_message,
                session_id=session_id,
                user_name=effective_user_name,
                portal_user_id=portal_user_id,
                portal_user_name=portal_user_name,
                reasoning_replay=reasoning_replay,
                attached_images=attached_images if attached_images else None,
                attachments=attachment_ids if attachment_ids else None,
                request_path="/api/chat",
                execution_metadata=execution_metadata,
                agent_id=runtime_agent_id,
                agent_name=runtime_agent_name,
                request_id=request_id,
                model=model,
                transient_model_message=transient_model_message,
            )
        execution_result = result.get("_execution_result")
        if runtime_agent_id and execution_result is not None:
            publish_metadata = await _enrich_publish_metadata_with_context_preview(
                execution_metadata,
                session_id=session_id,
            )
            publish_fields = extract_session_metadata_publish_fields(
                execution_result,
                metadata=publish_metadata,
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
        
        session = await session_manager.get_session(session_id)
        logger.info(f"[api_chat] EFP runtime session after chat: {session is not None}")
        if not session or not session.get("history"):
            logger.warning(f"[api_chat] No session or empty history for {session_id}")
        
        response_data = build_runtime_response_payload(
            result if isinstance(result, dict) else None,
            session_id,
        )
        usage = response_data.get("usage", {}) or {}
        
        # Record usage if available
        if usage:
            provider = global_config.llm.get('provider') or 'github_copilot'
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
            metadata = dict(session.get("metadata") or {})
            metadata["thinking_events"] = events
            session["metadata"] = metadata
            await session_manager.merge_metadata(session_id, {"thinking_events": events})
            logger.info(f"[api_chat] Saved {len(events)} thinking events to session metadata")
        
        # Include LLM debug info for sidebar display
        llm_debug = response_data.get("_llm_debug", {}) or {}
        
        # Always save thinking events to chatlog (even without llm_debug)
        chatlog_dir = os.path.join(runtime_session_artifacts.storage_dir, "chatlogs")
        os.makedirs(chatlog_dir, exist_ok=True)
        chatlog_file = os.path.join(chatlog_dir, f"{session_id}.json")
        try:
            context_state = response_data.get("context_state") or (
                result.get("context_state") if isinstance(result, dict) else None
            )
            chatlog_data = {
                "session_id": session_id,
                "request_id": response_data.get("request_id") or request_id,
                "status": "error" if response_data.get("error") else "success",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "metadata": session.get('metadata', {}),
                "events": events,
            }
            runtime_events = response_data.get("runtime_events")
            if isinstance(runtime_events, list):
                chatlog_data["runtime_events"] = runtime_events
                chatlog_data.setdefault("metadata", {})["runtime_events_count"] = len(runtime_events)
            if isinstance(context_state, dict):
                chatlog_data["context_state"] = context_state
                chatlog_data.setdefault("metadata", {})["context_state"] = context_state
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
        elif isinstance(e, RuntimeChatError):
            user_message = e.message
            error_type = e.error_type
            status_code = e.status_code
            error_details["details"] = e.details

        await _persist_chat_failure_state(
            agent_id=runtime_agent_id,
            session_id=session_id,
            request_id=request_id,
            user_message=user_message,
            error_type=error_type,
            metadata=execution_metadata,
        )

        error_response = {
            'error': user_message,
            'error_type': error_type,
            'details': error_details.get("details", {}),
            'timestamp': error_details.get("timestamp"),
        }
        if session_id:
            error_response['session_id'] = session_id
        if request_id:
            error_response['request_id'] = request_id
        return web.json_response(error_response, status=status_code)
    finally:
        try:
            if session_id and attachment_ids:
                await _cleanup_one_shot_attachments(session_id, attachment_ids)
        finally:
            clear_log_context()


async def api_chat_stream(request: web.Request) -> web.StreamResponse:
    """Handle streaming chat API requests (Server-Sent Events).

    POST /api/chat/stream
    Body: {"message": "...", "session_id": "optional"}

    Returns: text/event-stream with chunks of the response
    """
    response: Optional[web.StreamResponse] = None
    response_prepared = False
    run_task: Optional[asyncio.Task] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    runtime_agent_id: Optional[str] = None
    execution_metadata: Dict[str, Any] = {}
    attachment_ids: List[str] = []
    clear_log_context()
    trace_headers = _extract_task_trace_headers(request)
    set_log_context(
        trace_id=trace_headers.get("trace_id"),
        span_id=trace_headers.get("span_id"),
        parent_span_id=trace_headers.get("parent_span_id"),
        path="/api/chat/stream",
        runtime_type=os.getenv("EFP_RUNTIME_TYPE", "native"),
        execution_type="chat",
        source_type="runtime_api",
    )
    try:
        data = await request.json()
        original_user_text = (data.get('message') or '').strip()
        message = original_user_text
        session_id = _resolve_runtime_session_id(data)
        attachment_ids = _normalize_attachment_ids(data.get("attachments", []))
        portal_user_id, portal_user_name = _extract_portal_identity(request, data)
        execution_metadata = _extract_trusted_control_plane_metadata(request, data)
        client_request_id = _extract_trusted_client_request_id(request, data)
        request_id = client_request_id or f"chat-{uuid.uuid4()}"
        set_log_context(
            request_id=request_id,
            session_id=session_id,
        )
        effective_user_name = _resolve_chat_display_user_name(data, portal_user_name)

        attached_images = await _collect_attached_images(
            session_id=session_id,
            message=message,
            attachments=attachment_ids,
        )
        attachment_context = await _ensure_chat_attachment_context(session_id=session_id, attachment_ids=attachment_ids) if attachment_ids else {"context_file_ids": [], "failures": []}
        context_file_ids = attachment_context.get("context_file_ids", [])
        failures = attachment_context.get("failures", [])
        failure_notice = _build_attachment_parse_failure_notice(failures)
        if failures:
            execution_metadata["attachment_parse_failures"] = failures
        if failures and not context_file_ids and not attached_images:
            return web.json_response({"error": "attachment_parse_failed", "message": "One or more attached files could not be parsed.", "failures": failures, "session_id": session_id, "request_id": request_id}, status=400)

        if original_user_text:
            history_message = original_user_text
        elif context_file_ids:
            history_message = "[attachment]"
        elif attached_images:
            history_message = "[image]"
        else:
            history_message = ""

        model_context_query = original_user_text or ("Please summarize the attached file(s)." if context_file_ids else history_message)

        if not history_message.strip() and not attached_images and not attachment_ids:
            response = web.json_response({'error': 'Empty message'}, status=400)
            return response
        transient_model_message: Optional[str] = None
        if context_file_ids:
            transient_model_message, _citations, _source = _prepare_attachment_transient_model_message(
                session_id=session_id,
                context_file_ids=context_file_ids,
                model_context_query=model_context_query,
                top_k=5,
                max_tokens=4000,
            )
            if not transient_model_message:
                return web.json_response(
                    {
                        "error": "attachment_context_unavailable",
                        "message": "Attached file context could not be prepared for the model.",
                        "session_id": session_id,
                        "request_id": request_id,
                        "attachment_ids": context_file_ids,
                    },
                    status=400,
                )
            if failure_notice:
                transient_model_message = f"{transient_model_message}\n\n{failure_notice}"
        elif attached_images and failure_notice:
            transient_model_message = (
                f"{model_context_query or history_message or 'Please answer using the available image attachment(s).'}\n\n"
                f"{failure_notice}"
            )

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
        response_prepared = True

        # Send start event
        await response.write(
            _sse_event_bytes("start", build_stream_start_event_payload(session_id, request_id))
        )

        event_queue = asyncio.Queue()

        # Get request-scoped model (trusted portal override only)
        model_override = _extract_trusted_model_override(request, data)
        model = model_override or global_config.llm.get('model', DEFAULT_LLM_MODEL)

        # Run EFP runtime and stream runtime events where available.
        runtime_agent_id, runtime_agent_name = _resolve_runtime_agent_identity(request)
        set_log_context(agent_id=runtime_agent_id)
        runtime_event_projector = RuntimeEventProjector(
            request_id=request_id,
            agent_id=runtime_agent_id,
            agent_name=runtime_agent_name,
            model=model,
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
                message=history_message,
                session_id=session_id,
                user_name=effective_user_name,
                portal_user_id=portal_user_id,
                portal_user_name=portal_user_name,
                stream_callback=event_queue,
                attached_images=attached_images if attached_images else None,
                attachments=attachment_ids if attachment_ids else None,
                request_path="/api/chat/stream",
                execution_metadata=execution_metadata,
                agent_id=runtime_agent_id,
                agent_name=runtime_agent_name,
                request_id=request_id,
                model=model,
                transient_model_message=transient_model_message,
            )
        )

        while not run_task.done() or not event_queue.empty():
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.15)
                if isinstance(event, dict) and is_projected_runtime_event(event):
                    event_payloads = [event]
                else:
                    event_payloads = runtime_event_projector.project(event)
                for event_payload in event_payloads:
                    await _emit_gateway_runtime_event(event_payload)
                    await response.write(_sse_event_bytes("runtime_event", event_payload))
            except asyncio.TimeoutError:
                continue

        result = await run_task
        execution_result = result.get("_execution_result")
        if runtime_agent_id and execution_result is not None:
            publish_metadata = await _enrich_publish_metadata_with_context_preview(
                execution_metadata,
                session_id=session_id,
            )
            publish_fields = extract_session_metadata_publish_fields(
                execution_result,
                metadata=publish_metadata,
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
            provider = global_config.llm.get('provider') or 'github_copilot'
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
        usage_data = {
            'usage': usage,
            'session_id': session_id,
            'request_id': request_id,
        }
        await response.write(_sse_event_bytes("usage", usage_data))

        response_data = build_runtime_response_payload(
            result if isinstance(result, dict) else None,
            session_id,
        )
        response_data.setdefault("request_id", request_id)
        await response.write(_sse_event_bytes("final", response_data))

        # Send done event
        await response.write(_sse_event_bytes("done", {
            "ok": True,
            "session_id": session_id,
            "request_id": request_id,
        }))

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
        stream_status_code = 500
        stream_error_type = type(e).__name__
        stream_details: Dict[str, Any] = {}
        if isinstance(e, RuntimeChatError):
            stream_status_code = e.status_code
            stream_error_type = e.error_type
            stream_details = e.details
        await _persist_chat_failure_state(
            agent_id=runtime_agent_id,
            session_id=session_id,
            request_id=request_id,
            user_message=str(e),
            error_type=stream_error_type,
            metadata=execution_metadata,
        )
        if response is not None and response_prepared:
            try:
                await response.write(
                    _sse_event_bytes(
                        "error",
                        {
                            "error": str(e),
                            "error_type": stream_error_type,
                            "details": stream_details,
                            "session_id": session_id,
                            "request_id": request_id,
                        },
                    )
                )
                await response.write(_sse_event_bytes("done", {"ok": False, "session_id": session_id, "request_id": request_id}))
            except Exception:
                pass
            return response
        return web.json_response(
            {'error': str(e), "error_type": stream_error_type, "details": stream_details},
            status=stream_status_code,
        )
    finally:
        try:
            if run_task is not None and not run_task.done():
                try:
                    await run_task
                except Exception:
                    pass
            if session_id and attachment_ids:
                await _cleanup_one_shot_attachments(session_id, attachment_ids)
        finally:
            clear_log_context()


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
        task_session_id = _resolve_runtime_task_session_id(
            task_id=parsed["task_id"],
            task_type=parsed["task_type"],
            session_id=parsed["session_id"],
            input_payload=merged_input_payload,
            metadata=metadata,
        )
        if task_session_id:
            merged_input_payload["task_session_id"] = task_session_id
            metadata["runtime_task_session_id"] = task_session_id
        input_delivery = _resolve_runtime_task_delivery(merged_input_payload, metadata)
        metadata["runtime_task_delivery"] = input_delivery
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
        existing_record = runtime_task_tracker.get(parsed["task_id"])
        if existing_record is not None:
            if not runtime_task_tracker.admission_matches(
                existing_record,
                task_type=parsed["task_type"],
                source=parsed["source"] or "portal",
                session_id=parsed["session_id"],
                task_session_id=task_session_id,
                input_delivery=input_delivery,
                context_ref=parsed["context_ref"] or None,
                merged_input_payload=merged_input_payload,
                metadata=metadata,
            ):
                return web.json_response(
                    {
                        "ok": False,
                        "task_id": parsed["task_id"],
                        "execution_type": "task",
                        "request_id": existing_record.request_id,
                        "status": existing_record.status,
                        "error": "task_id_conflicts_with_existing_admission",
                        "admission_id": existing_record.admission_id,
                        "input_hash": existing_record.input_hash,
                        "engine": "native",
                    },
                    status=409,
                )
            if existing_record.status in {"accepted", "running"}:
                try:
                    _schedule_runtime_task_record(existing_record)
                except Exception as exc:
                    sanitized_message = sanitize_exception_message(exc)
                    logger.error("Task execute duplicate scheduling failed | task_id=%s", parsed["task_id"], exc_info=True)
                    logger.debug("Task execute duplicate scheduling error detail: %s", sanitized_message)
                    failure_payload = {
                        "ok": False,
                        "task_id": parsed["task_id"],
                        "execution_type": "task",
                        "request_id": existing_record.request_id,
                        "status": "error",
                        "trace_id": existing_record.trace_id,
                        "portal_dispatch_id": existing_record.portal_dispatch_id,
                        "error": sanitized_message,
                    }
                    runtime_task_tracker.mark_internal_failure(
                        parsed["task_id"],
                        payload=failure_payload,
                        error_message=sanitized_message,
                    )
                    return web.json_response(failure_payload, status=500)
            return web.json_response(_runtime_task_status_payload(existing_record), status=200)

        record = runtime_task_tracker.create_pending(
            task_id=parsed["task_id"],
            request_id=request_id,
            task_type=parsed["task_type"],
            source=parsed["source"] or "portal",
            session_id=parsed["session_id"],
            agent_id=runtime_agent_id,
            trace_id=trace_headers.get("trace_id"),
            portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
            portal_task_id=metadata.get("portal_task_id"),
            context_ref=parsed["context_ref"] or None,
            merged_input_payload=merged_input_payload,
            metadata=metadata,
            trace_headers=trace_headers,
            task_session_id=task_session_id,
            input_delivery=input_delivery,
        )

        try:
            _schedule_runtime_task_record(record)
        except Exception as exc:
            runtime_task_tracker.remove(parsed["task_id"])
            logger.error("Task execute scheduling failed | task_id=%s", parsed["task_id"], exc_info=True)
            logger.debug("Task execute scheduling error detail: %s", sanitize_exception_message(exc))
            return web.json_response({"error": "Internal server error"}, status=500)

        await _emit_task_lifecycle_event(
            "task.accepted",
            task_id=parsed["task_id"],
            portal_task_id=metadata.get("portal_task_id"),
            agent_id=runtime_agent_id,
            session_id=parsed["session_id"] or task_session_id,
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
                "engine": "native",
                "admission_id": record.admission_id,
                "input_hash": record.input_hash,
                "task_session_id": record.task_session_id,
                "delivery": record.input_delivery,
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


def _runtime_task_session_key(record: Any) -> Optional[str]:
    return getattr(record, "task_session_id", None) or getattr(record, "session_id", None)


def _runtime_task_waiting_for_user(record: Any) -> bool:
    return bool(
        getattr(record, "status", None) == "blocked"
        and (
            getattr(record, "pending_permission_request", None) is not None
            or getattr(record, "pending_question_request", None) is not None
        )
    )


def _rehydrate_waiting_runtime_task_session_lane(session_key: Optional[str]) -> Optional[Any]:
    if not session_key or runtime_task_session_coordinator.active_task_id(session_key):
        return None
    finder = getattr(runtime_task_tracker, "find_waiting_for_user_by_session", None)
    waiting_record = finder(session_key) if callable(finder) else None
    if waiting_record is None:
        return None
    decision = runtime_task_session_coordinator.schedule(
        session_key,
        waiting_record.task_id,
        admitted_seq=int(getattr(waiting_record, "admitted_seq", 0) or 0),
        delivery=str(getattr(waiting_record, "input_delivery", "steer") or "steer"),
    )
    if decision.action not in {"start", "active"}:
        return None
    runtime_task_session_coordinator.hold_for_user_input(session_key, waiting_record.task_id)
    logger.info(
        "Runtime task session lane rehydrated for pending user input | task_id=%s session_id=%s",
        waiting_record.task_id,
        session_key,
    )
    return waiting_record


def _rehydrate_waiting_runtime_task_session_lanes() -> int:
    lister = getattr(runtime_task_tracker, "list_waiting_for_user", None)
    waiting_records = lister() if callable(lister) else []
    rehydrated_count = 0
    for record in waiting_records:
        session_key = _runtime_task_session_key(record)
        if not session_key:
            continue
        rehydrated = _rehydrate_waiting_runtime_task_session_lane(session_key)
        if rehydrated is not None and rehydrated.task_id == record.task_id:
            rehydrated_count += 1
    return rehydrated_count


def _reconcile_runtime_task_session_lane(session_key: Optional[str]) -> None:
    if not session_key:
        return
    active_task_id = runtime_task_session_coordinator.active_task_id(session_key)
    if not active_task_id:
        _rehydrate_waiting_runtime_task_session_lane(session_key)
        return
    active_record = runtime_task_tracker.get(active_task_id)
    if active_record is None:
        runtime_task_session_coordinator.clear(session_key)
        _rehydrate_waiting_runtime_task_session_lane(session_key)
        return
    if _runtime_task_waiting_for_user(active_record):
        return
    background_task = getattr(active_record, "background_task", None)
    active_status = getattr(active_record, "status", None)
    if active_status in {"accepted", "running"} and background_task is not None and not background_task.done():
        return
    if active_status in {"accepted", "running"} and background_task is None:
        return
    runtime_task_session_coordinator.complete(session_key, active_task_id)


def _schedule_next_runtime_task_session_record(next_task_id: Optional[str]) -> None:
    if not next_task_id:
        return
    next_record = runtime_task_tracker.get(next_task_id)
    if next_record is None or next_record.status not in {"accepted", "running"}:
        return
    try:
        _schedule_runtime_task_record(next_record)
    except Exception:
        logger.warning("Failed to schedule next runtime task session record | task_id=%s", next_task_id, exc_info=True)


def _runtime_task_observability_fields(record: Any) -> Dict[str, Any]:
    session_key = _runtime_task_session_key(record)
    lane_snapshot = runtime_task_session_coordinator.snapshot(session_key, record.task_id) if session_key else None
    return {
        "engine": "native",
        "admission_id": getattr(record, "admission_id", None),
        "admitted_seq": getattr(record, "admitted_seq", 0),
        "admitted_at": getattr(record, "admitted_at", None),
        "delivery": getattr(record, "input_delivery", "steer"),
        "input_hash": getattr(record, "input_hash", None),
        "task_session_id": getattr(record, "task_session_id", None),
        "session_lane_status": lane_snapshot.lane_status if lane_snapshot else None,
        "session_active_task_id": lane_snapshot.active_task_id if lane_snapshot else None,
        "session_queue_position": lane_snapshot.queue_position if lane_snapshot else None,
        "session_pending_count": lane_snapshot.pending_count if lane_snapshot else 0,
        "session_interrupt_seq": lane_snapshot.interrupt_seq if lane_snapshot else None,
        "active_attempt_id": getattr(record, "active_attempt_id", None),
        "attempt_count": getattr(record, "attempt_count", 0),
        "last_attempt_started_at": getattr(record, "last_attempt_started_at", None),
        "last_progress_at": getattr(record, "last_progress_at", None),
        "pending_permission_request": getattr(record, "pending_permission_request", None),
        "pending_question_request": getattr(record, "pending_question_request", None),
        "completion_source": getattr(record, "completion_source", None),
        "cancel_requested": getattr(record, "cancel_requested", False),
    }


def _runtime_task_status_payload(record: Any) -> Dict[str, Any]:
    if record.status in {"accepted", "running"}:
        return {
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
            "resume_count": getattr(record, "resume_count", 0),
            "last_resumed_at": getattr(record, "last_resumed_at", None),
            **_runtime_task_observability_fields(record),
        }
    payload = dict(record.payload)
    payload["accepted_at"] = record.accepted_at
    payload["started_at"] = record.started_at
    payload["finished_at"] = record.finished_at
    payload["resume_count"] = getattr(record, "resume_count", 0)
    payload["last_resumed_at"] = getattr(record, "last_resumed_at", None)
    payload.update(_runtime_task_observability_fields(record))
    return payload


def _has_runtime_task_resume_payload(record: Any) -> bool:
    return isinstance(getattr(record, "merged_input_payload", None), dict) and isinstance(getattr(record, "metadata", None), dict)


def _schedule_runtime_task_record(record: Any, *, resumed: bool = False) -> bool:
    if record.status not in {"accepted", "running"}:
        return False
    background_task = getattr(record, "background_task", None)
    if background_task is not None and not background_task.done():
        return False
    if not _has_runtime_task_resume_payload(record):
        runtime_task_tracker.mark_stale(
            record.task_id,
            reason="Persisted runtime task request is incomplete",
            payload={
                "ok": False,
                "task_id": record.task_id,
                "execution_type": "task",
                "request_id": record.request_id,
                "status": "stale",
                "trace_id": record.trace_id,
                "portal_dispatch_id": record.portal_dispatch_id,
                "error": "Persisted runtime task request is incomplete",
            },
        )
        return False

    metadata = dict(record.metadata or {})
    trace_headers = dict(record.trace_headers or {})
    if not trace_headers.get("trace_id"):
        trace_headers["trace_id"] = record.trace_id
    if not trace_headers.get("portal_dispatch_id"):
        trace_headers["portal_dispatch_id"] = record.portal_dispatch_id
    if resumed:
        metadata["runtime_task_resumed"] = True
        metadata["runtime_task_resume_count"] = getattr(record, "resume_count", 0)
    metadata["runtime_task_admission_id"] = getattr(record, "admission_id", None)
    metadata["runtime_task_input_hash"] = getattr(record, "input_hash", None)
    metadata["runtime_task_session_id"] = getattr(record, "task_session_id", None)
    metadata["runtime_task_delivery"] = getattr(record, "input_delivery", "steer")
    metadata["runtime_task_attempt_count"] = getattr(record, "attempt_count", 0)
    execution_session_id = record.session_id or getattr(record, "task_session_id", None)
    session_key = _runtime_task_session_key(record) or execution_session_id
    if session_key:
        _reconcile_runtime_task_session_lane(session_key)
        decision = runtime_task_session_coordinator.schedule(
            session_key,
            record.task_id,
            admitted_seq=int(getattr(record, "admitted_seq", 0) or 0),
            delivery=str(getattr(record, "input_delivery", "steer") or "steer"),
        )
        if decision.action == "queued":
            logger.info(
                "Runtime task queued behind active session lane | task_id=%s session_id=%s active_task_id=%s queue_position=%s pending_count=%s",
                record.task_id,
                session_key,
                decision.active_task_id or "-",
                decision.queue_position or "-",
                decision.pending_count,
            )
            return True
        if decision.action == "active":
            logger.debug(
                "Runtime task owns active session lane | task_id=%s session_id=%s",
                record.task_id,
                session_key,
            )
        if decision.action == "suppressed":
            runtime_task_tracker.mark_stale(
                record.task_id,
                reason="Runtime task suppressed by session interrupt",
                payload={
                    "ok": False,
                    "task_id": record.task_id,
                    "execution_type": "task",
                    "request_id": record.request_id,
                    "status": "stale",
                    "trace_id": record.trace_id,
                    "portal_dispatch_id": record.portal_dispatch_id,
                    "error": "Runtime task suppressed by session interrupt",
                },
            )
            return False

    background_coro = _run_task_execution_in_background(
        task_id=record.task_id,
        request_id=record.request_id,
        task_type=record.task_type,
        session_id=execution_session_id,
        source=record.source or "portal",
        runtime_agent_id=record.agent_id,
        context_ref=record.context_ref or None,
        merged_input_payload=dict(record.merged_input_payload or {}),
        metadata=metadata,
        trace_headers=trace_headers,
    )
    try:
        spawned = _spawn_runtime_background_task(background_coro)
    except Exception:
        background_coro.close()
        if session_key:
            runtime_task_session_coordinator.complete(session_key, record.task_id)
        raise
    runtime_task_tracker.set_background_task(record.task_id, spawned)
    return True


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
    attempt_id: Optional[str] = None
    metadata_session_key = metadata.get("runtime_task_session_id") if isinstance(metadata, dict) else None
    session_key = (
        _safe_runtime_task_session_id(metadata_session_key)
        if isinstance(metadata_session_key, str) and metadata_session_key.strip()
        else session_id
    )
    try:
        running_record = runtime_task_tracker.mark_running(task_id)
        if running_record is not None:
            attempt_id = getattr(running_record, "active_attempt_id", None)
            metadata = dict(metadata)
            metadata["runtime_task_admission_id"] = getattr(running_record, "admission_id", None)
            metadata["runtime_task_input_hash"] = getattr(running_record, "input_hash", None)
            metadata["runtime_task_session_id"] = getattr(running_record, "task_session_id", None)
            metadata["runtime_task_attempt_id"] = attempt_id
            metadata["runtime_task_attempt_count"] = getattr(running_record, "attempt_count", 0)
        await _emit_task_lifecycle_event(
            "task.started",
            task_id=task_id,
            portal_task_id=metadata.get("portal_task_id"),
            agent_id=runtime_agent_id,
            session_id=session_id,
            trace_id=trace_headers.get("trace_id"),
            portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
        )
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
            publish_metadata = await _enrich_publish_metadata_with_context_preview(
                metadata,
                session_id=session_id,
            )
            publish_fields = extract_session_metadata_publish_fields(
                execution_result,
                metadata=publish_metadata,
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

        current = runtime_task_tracker.get(task_id)
        if current is not None and current.status == "cancelled":
            logger.info("Task result ignored because task already cancelled | task_id=%s", task_id)
            return

        response_payload = _build_runtime_task_terminal_payload(
            task_id=task_id,
            execution_result=execution_result,
            trace_headers=trace_headers,
        )
        current = runtime_task_tracker.get(task_id)
        if current is not None:
            response_payload.update(_runtime_task_observability_fields(current))
        status = str(execution_result.status or "error")
        terminal_record = runtime_task_tracker.mark_terminal(
            task_id,
            status=status,
            payload=response_payload,
            error_message=str(response_payload.get("error") or "") or None,
            attempt_id=attempt_id,
            completion_source="execution_result",
        )
        if terminal_record is None:
            logger.info("Task result ignored because attempt is no longer current | task_id=%s attempt_id=%s", task_id, attempt_id or "-")
            return
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
    except asyncio.CancelledError:
        current = runtime_task_tracker.get(task_id)
        if current is None or current.status != "cancelled":
            cancelled_payload = {
                "ok": False,
                "task_id": task_id,
                "execution_type": "task",
                "request_id": request_id,
                "status": "cancelled",
                "trace_id": trace_headers.get("trace_id"),
                "portal_dispatch_id": trace_headers.get("portal_dispatch_id"),
                "error": "Task cancelled",
            }
            current = runtime_task_tracker.get(task_id)
            if current is not None:
                cancelled_payload.update(_runtime_task_observability_fields(current))
            runtime_task_tracker.cancel(
                task_id,
                reason="Task cancelled",
                payload=cancelled_payload,
            )
            await _emit_task_lifecycle_event(
                "task.cancelled",
                task_id=task_id,
                portal_task_id=metadata.get("portal_task_id"),
                agent_id=runtime_agent_id,
                session_id=session_id,
                trace_id=trace_headers.get("trace_id"),
                portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
            )
        logger.info("Task execution background cancelled | task_id=%s", task_id)
        return
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
        current = runtime_task_tracker.get(task_id)
        if current is not None:
            failure_payload.update(_runtime_task_observability_fields(current))
        terminal_record = runtime_task_tracker.mark_internal_failure(
            task_id,
            payload=failure_payload,
            error_message=sanitized_message,
            attempt_id=attempt_id,
        )
        if terminal_record is None:
            logger.info("Task failure ignored because attempt is no longer current | task_id=%s attempt_id=%s", task_id, attempt_id or "-")
            return
        await _emit_task_lifecycle_event(
            "task.failed",
            task_id=task_id,
            portal_task_id=metadata.get("portal_task_id"),
            agent_id=runtime_agent_id,
            session_id=session_id,
            trace_id=trace_headers.get("trace_id"),
            portal_dispatch_id=trace_headers.get("portal_dispatch_id"),
        )
    finally:
        next_task_id: Optional[str] = None
        if session_key:
            current = runtime_task_tracker.get(task_id)
            if current is not None and _runtime_task_waiting_for_user(current):
                runtime_task_session_coordinator.hold_for_user_input(session_key, task_id)
            elif current is None or getattr(current, "status", None) in {"success", "error", "blocked", "cancelled", "stale"}:
                next_task_id = runtime_task_session_coordinator.complete(session_key, task_id)
        _schedule_next_runtime_task_session_record(next_task_id)


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
        return web.json_response(_runtime_task_status_payload(record))
    finally:
        clear_log_context()




async def api_task_cancel(request: web.Request) -> web.Response:
    clear_log_context()
    trace_headers = _extract_task_trace_headers(request)
    set_log_context(path="/api/tasks/{task_id}/cancel", trace_id=trace_headers.get("trace_id"))
    try:
        task_id = str(request.match_info.get("task_id") or "").strip()
        if not task_id:
            return web.json_response({"error": "task_id is required"}, status=400)
        record = runtime_task_tracker.get(task_id)
        if record is None:
            return web.json_response({"error": "Task not found"}, status=404)
        waiting_for_user = _runtime_task_waiting_for_user(record)
        if record.status in {"success", "error", "blocked", "cancelled", "stale"} and not waiting_for_user:
            payload = _runtime_task_status_payload(record)
            payload["cancel_requested"] = True
            payload["status"] = record.status
            return web.json_response(payload)
        session_key = _runtime_task_session_key(record)
        if session_key:
            runtime_task_session_coordinator.cancel(
                session_key,
                task_id,
                admitted_seq=int(getattr(record, "admitted_seq", 0) or 0),
            )
        payload = {"ok": False, "task_id": task_id, "execution_type": "task", "request_id": record.request_id, "status": "cancelled", "cancel_requested": True, "trace_id": record.trace_id, "portal_dispatch_id": record.portal_dispatch_id, "accepted_at": record.accepted_at, "started_at": record.started_at, "finished_at": None, "error": "Task cancelled by request"}
        payload.update(_runtime_task_observability_fields(record))
        cancelled = runtime_task_tracker.cancel(task_id, reason="Task cancelled by request", payload=payload, force=waiting_for_user)
        if cancelled:
            payload = _runtime_task_status_payload(cancelled)
        if waiting_for_user:
            next_task_id = runtime_task_session_coordinator.complete(session_key, task_id) if session_key else None
            _schedule_next_runtime_task_session_record(next_task_id)
        await _emit_task_lifecycle_event("task.cancelled", task_id=task_id, portal_task_id=record.portal_task_id, agent_id=record.agent_id, session_id=record.session_id, trace_id=record.trace_id, portal_dispatch_id=record.portal_dispatch_id)
        return web.json_response(payload)
    finally:
        clear_log_context()


def _pending_request_id(pending: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(pending, dict):
        return None
    value = pending.get("request_id") or pending.get("id")
    return str(value).strip() if value is not None and str(value).strip() else None


def _pending_request_matches(pending: Optional[Dict[str, Any]], request_id: Optional[str]) -> bool:
    pending_id = _pending_request_id(pending)
    if request_id:
        return pending_id == request_id
    return pending_id is not None


def _permission_response_decision(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"approve", "approved", "allow", "allowed", "accept", "accepted"}:
        return "approve"
    if normalized in {"deny", "denied", "reject", "rejected"}:
        return "deny"
    raise ValueError("decision must be approve/allow or deny/reject")


def _metadata_with_permission_response(
    metadata: Optional[Dict[str, Any]],
    *,
    pending_request: Dict[str, Any],
    decision: str,
    always: bool,
    reason: Optional[str],
) -> Dict[str, Any]:
    merged = dict(metadata or {})
    profile = dict(merged.get("runtime_profile") or {}) if isinstance(merged.get("runtime_profile"), dict) else {}
    profile_config = dict(profile.get("config") or {}) if isinstance(profile.get("config"), dict) else {}
    tool_permissions = dict(profile_config.get("tool_permissions") or {}) if isinstance(profile_config.get("tool_permissions"), dict) else {}
    request_metadata = pending_request.get("metadata") if isinstance(pending_request.get("metadata"), dict) else {}
    tool_id = (
        pending_request.get("tool_id")
        or pending_request.get("tool")
        or request_metadata.get("tool_name")
    )
    if not isinstance(tool_id, str) or not tool_id.strip():
        raise ValueError("pending permission request is missing tool_id")
    rule: Dict[str, Any] = {"action": "allow" if decision == "approve" else "deny"}
    patterns = pending_request.get("patterns")
    if isinstance(patterns, list) and patterns:
        rule["patterns"] = patterns
    elif pending_request.get("args") is not None:
        rule["patterns"] = [json.dumps(pending_request.get("args"), ensure_ascii=False, sort_keys=True, default=str)]
    if reason:
        rule["reason"] = reason
    tool_permissions[tool_id.strip()] = rule
    profile_config["tool_permissions"] = tool_permissions
    profile["source"] = "portal.runtime_profile"
    profile["config"] = profile_config
    merged["runtime_profile"] = profile
    merged["runtime_task_resume_after_user_input"] = True
    merged["runtime_permission_response"] = {
        "request_id": _pending_request_id(pending_request),
        "decision": decision,
        "always": bool(always),
        "reason": reason,
        "tool_id": tool_id.strip(),
    }
    return merged


def _metadata_with_question_response(
    metadata: Optional[Dict[str, Any]],
    *,
    pending_request: Dict[str, Any],
    answers: Any,
) -> Dict[str, Any]:
    merged = dict(metadata or {})
    merged["runtime_task_resume_after_user_input"] = True
    merged["runtime_question_response"] = {
        "request_id": _pending_request_id(pending_request),
        "request": dict(pending_request),
        "answers": answers,
    }
    return merged


def _resume_runtime_task_after_user_input(record: Any, *, metadata: Dict[str, Any]) -> Any:
    merged_input_payload = dict(record.merged_input_payload or {})
    merged_input_payload["_runtime_resume"] = True
    resumed = runtime_task_tracker.resume_after_user_input(
        record.task_id,
        merged_input_payload=merged_input_payload,
        metadata=metadata,
    )
    if resumed is None:
        return None
    _schedule_runtime_task_record(resumed)
    return resumed


async def api_task_permission_respond(request: web.Request) -> web.Response:
    clear_log_context()
    trace_headers = _extract_task_trace_headers(request)
    set_log_context(path="/api/tasks/{task_id}/permission/respond", trace_id=trace_headers.get("trace_id"))
    try:
        task_id = str(request.match_info.get("task_id") or "").strip()
        if not task_id:
            return web.json_response({"error": "task_id is required"}, status=400)
        data = await request.json()
        if not isinstance(data, dict):
            return web.json_response({"error": "Request body must be a JSON object"}, status=400)
        record = runtime_task_tracker.get(task_id)
        if record is None:
            return web.json_response({"error": "Task not found"}, status=404)
        pending = getattr(record, "pending_permission_request", None)
        if not isinstance(pending, dict):
            return web.json_response({"error": "Task is not waiting for permission"}, status=409)
        request_id = str(data.get("request_id") or data.get("id") or "").strip() or None
        if not _pending_request_matches(pending, request_id):
            return web.json_response({"error": "permission_request_id_mismatch"}, status=409)
        decision = _permission_response_decision(data.get("decision") or data.get("action") or data.get("reply"))
        metadata = _metadata_with_permission_response(
            record.metadata,
            pending_request=pending,
            decision=decision,
            always=bool(data.get("always")),
            reason=str(data.get("reason")).strip() if data.get("reason") is not None else None,
        )
        resumed = _resume_runtime_task_after_user_input(record, metadata=metadata)
        if resumed is None:
            return web.json_response({"error": "Task not found"}, status=404)
        return web.json_response(_runtime_task_status_payload(resumed), status=202)
    except (json.JSONDecodeError, ContentTypeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    finally:
        clear_log_context()


async def api_task_question_respond(request: web.Request) -> web.Response:
    clear_log_context()
    trace_headers = _extract_task_trace_headers(request)
    set_log_context(path="/api/tasks/{task_id}/question/respond", trace_id=trace_headers.get("trace_id"))
    try:
        task_id = str(request.match_info.get("task_id") or "").strip()
        if not task_id:
            return web.json_response({"error": "task_id is required"}, status=400)
        data = await request.json()
        if not isinstance(data, dict):
            return web.json_response({"error": "Request body must be a JSON object"}, status=400)
        record = runtime_task_tracker.get(task_id)
        if record is None:
            return web.json_response({"error": "Task not found"}, status=404)
        pending = getattr(record, "pending_question_request", None)
        if not isinstance(pending, dict):
            return web.json_response({"error": "Task is not waiting for a question response"}, status=409)
        request_id = str(data.get("request_id") or data.get("id") or "").strip() or None
        if not _pending_request_matches(pending, request_id):
            return web.json_response({"error": "question_request_id_mismatch"}, status=409)
        if "answers" in data:
            answers = data.get("answers")
        elif "answer" in data:
            answers = data.get("answer")
        else:
            return web.json_response({"error": "answers is required"}, status=400)
        metadata = _metadata_with_question_response(
            record.metadata,
            pending_request=pending,
            answers=answers,
        )
        resumed = _resume_runtime_task_after_user_input(record, metadata=metadata)
        if resumed is None:
            return web.json_response({"error": "Task not found"}, status=404)
        return web.json_response(_runtime_task_status_payload(resumed), status=202)
    except (json.JSONDecodeError, ContentTypeError):
        return web.json_response({"error": "Invalid JSON"}, status=400)
    finally:
        clear_log_context()


def _runtime_task_persistence_storage_dir() -> Optional[Path]:
    enabled = str(os.getenv("EFP_RUNTIME_TASKS_PERSISTENCE", "true")).strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None
    explicit = str(os.getenv("EFP_RUNTIME_TASKS_DIR", "")).strip()
    if explicit:
        return Path(explicit).expanduser()
    try:
        config_data = global_config.get_effective_config()
    except Exception:
        config_data = getattr(global_config, "_config", {}) or {}
    return resolve_runtime_workspace(config_data) / ".efp" / "runtime_tasks"


async def resume_persisted_runtime_tasks() -> int:
    storage_dir = _runtime_task_persistence_storage_dir()
    if storage_dir is None:
        runtime_task_tracker.configure_storage(None)
        return 0

    runtime_task_tracker.configure_storage(storage_dir)
    loaded_count = runtime_task_tracker.load_persisted_records()
    rehydrated_waiting_count = _rehydrate_waiting_runtime_task_session_lanes()
    resumed_count = 0
    for record in runtime_task_tracker.list_active():
        background_task = getattr(record, "background_task", None)
        if background_task is not None and not background_task.done():
            continue
        resumed_record = runtime_task_tracker.mark_resuming(record.task_id) or record
        try:
            scheduled = _schedule_runtime_task_record(resumed_record, resumed=True)
        except Exception as exc:
            sanitized_message = sanitize_exception_message(exc)
            logger.error("Persisted runtime task resume failed | task_id=%s", record.task_id, exc_info=True)
            failure_payload = {
                "ok": False,
                "task_id": record.task_id,
                "execution_type": "task",
                "request_id": record.request_id,
                "status": "error",
                "trace_id": record.trace_id,
                "portal_dispatch_id": record.portal_dispatch_id,
                "error": sanitized_message,
            }
            current = runtime_task_tracker.get(record.task_id)
            if current is not None:
                failure_payload.update(_runtime_task_observability_fields(current))
            runtime_task_tracker.mark_internal_failure(
                record.task_id,
                payload=failure_payload,
                error_message=sanitized_message,
            )
            continue
        if not scheduled:
            continue
        resumed_count += 1
        await _emit_task_lifecycle_event(
            "task.resumed",
            task_id=record.task_id,
            portal_task_id=record.portal_task_id,
            agent_id=record.agent_id,
            session_id=record.session_id,
            trace_id=record.trace_id,
            portal_dispatch_id=record.portal_dispatch_id,
        )
    if loaded_count or resumed_count:
        logger.info(
            "Runtime task recovery initialized | storage_dir=%s loaded=%s resumed=%s waiting_lanes=%s",
            storage_dir,
            loaded_count,
            resumed_count,
            rehydrated_waiting_count,
        )
    return resumed_count


async def _resume_runtime_tasks_on_startup(_app: web.Application) -> None:
    await resume_persisted_runtime_tasks()


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
    
    """
    import time
    start_time = time.time()
    logger.info("[api_sessions] Request start")
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
    clear_log_context()
    trace_headers = _extract_task_trace_headers(request)
    set_log_context(
        trace_id=trace_headers.get("trace_id"),
        span_id=trace_headers.get("span_id"),
        parent_span_id=trace_headers.get("parent_span_id"),
        path="/api/sessions/{session_id}",
        runtime_type=os.getenv("EFP_RUNTIME_TYPE", "native"),
        execution_type="session",
        source_type="runtime_api",
    )
    try:
        # Initialize session manager if needed
        if not session_manager._initialized:
            await session_manager.initialize()
        
        session_id = request.match_info.get('session_id', '')
        set_log_context(session_id=session_id)
        if not session_id:
            return web.json_response({'error': 'Session ID required'}, status=400)
        
        session_info = await session_manager.get_existing_session(session_id)
        
        if not session_info:
            return web.json_response({'error': 'Session not found'}, status=404)
        
        portal_user_id, portal_user_name = _extract_portal_identity(request, {})
        runtime_agent_id, runtime_agent_name = _resolve_runtime_agent_identity(request)
        set_log_context(agent_id=runtime_agent_id)
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
        logger.error(f"[api_load_session] ERROR: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)
    finally:
        clear_log_context()


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
        chatlog_file = os.path.join(runtime_session_artifacts.storage_dir, "chatlogs", f"{session_id}.json")
        
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
                    "external_config_status": global_config.get_external_config_status(),
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
                "external_config_status": global_config.get_external_config_status(),
            }
        )
    except Exception as e:
        logger.error("Error applying runtime profile config: %s", e, exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)


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


def setup_runtime_api_routes(app: web.Application):
    """Register API-only runtime routes used by Portal and runtime clients."""
    from src.gateway.server_files import setup_server_files_routes

    if not any(handler is _resume_runtime_tasks_on_startup for handler in app.on_startup):
        app.on_startup.append(_resume_runtime_tasks_on_startup)

    app.router.add_post('/api/chat', api_chat)
    app.router.add_post('/api/chat/stream', api_chat_stream)
    app.router.add_post('/api/tasks/execute', api_tasks_execute)
    app.router.add_get('/api/tasks/{task_id}', api_task_status)
    app.router.add_post('/api/tasks/{task_id}/cancel', api_task_cancel)
    app.router.add_post('/api/tasks/{task_id}/permission/respond', api_task_permission_respond)
    app.router.add_post('/api/tasks/{task_id}/question/respond', api_task_question_respond)
    app.router.add_get('/api/capabilities', api_capabilities)
    app.router.add_get('/api/sessions', api_sessions)
    app.router.add_get('/api/sessions/{session_id}', api_load_session)
    app.router.add_post('/api/sessions/{session_id}/rename', api_rename_session)
    app.router.add_delete('/api/sessions/{session_id}', api_delete_session)
    app.router.add_get('/api/sessions/{session_id}/chatlog', api_session_chatlog)
    app.router.add_get('/api/usage', api_usage)
    app.router.add_post('/api/internal/runtime-profile/apply', api_apply_runtime_profile)
    app.router.add_get('/api/skills', api_skills)
    setup_server_files_routes(app)

    logger.info("Runtime API routes registered:")
    logger.info("  POST /api/chat     - Send message")
    logger.info("  POST /api/chat/stream - Send message (streaming SSE)")
    logger.info("  POST /api/tasks/execute - Accept and execute structured runtime task")
    logger.info("  GET  /api/tasks/{task_id} - Runtime task status for portal polling")
    logger.info("  POST /api/tasks/{task_id}/cancel - Cancel runtime task")
    logger.info("  GET  /api/sessions - List recent sessions")
    logger.info("  GET  /api/sessions/{id} - Load session messages")
    logger.info("  POST /api/sessions/{id}/rename - Rename existing session")
    logger.info("  DELETE /api/sessions/{id} - Delete existing session")
    logger.info("  GET  /api/usage   - Get usage stats")
    logger.info("  GET  /api/skills  - Get available skills")
    logger.info("  GET  /api/server-files - Browse workspace server files")
    logger.info("  GET  /api/server-files/read - Read workspace text file")
    logger.info("  GET  /api/server-files/content - Stream workspace file content")
    logger.info("  POST /api/server-files/upload - Upload workspace file")
    logger.info("  POST /api/server-files/delete - Delete workspace file or directory")
    logger.info("  GET  /api/server-files/download - Download workspace file or archive")
