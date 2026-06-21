"""Gateway-facing facade over the EFP runtime file session store."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import importlib
import logging
import os
from pathlib import Path
from threading import RLock
import uuid
from typing import Any, Callable, Iterable, Mapping, Optional

from ..types import ToolCall, ToolResult
from .file_store import FileSessionStore
from .models import (
    CompactionPart,
    Message,
    MessagePart,
    MessagePartType,
    MessageRole,
    Session,
)

logger = logging.getLogger(__name__)

JIRA_SESSION_PREFIX = "jira:"
RUNTIME_SESSION_ROOT_ENV = "EFP_RUNTIME_SESSION_ROOT"
RUNTIME_WORKSPACE_DIR_ENV = "EFP_WORKSPACE_DIR"
DEFAULT_MAX_HISTORY = 999999
DEFAULT_AUTO_SAVE = True

_singleton_lock = RLock()
_store_singletons: dict[Path, FileSessionStore] = {}
_manager_singletons: dict[Path, "RuntimeSessionManager"] = {}


def runtime_session_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser().resolve()
    configured = str(os.environ.get(RUNTIME_SESSION_ROOT_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    workspace = str(os.environ.get(RUNTIME_WORKSPACE_DIR_ENV) or "").strip()
    if workspace:
        return (Path(workspace).expanduser() / ".efp" / "runtime").resolve()
    return (Path.home() / ".efp" / "runtime").resolve()


def get_runtime_session_store(root: str | Path | None = None) -> FileSessionStore:
    resolved = runtime_session_root(root)
    with _singleton_lock:
        store = _store_singletons.get(resolved)
        if store is None:
            store = FileSessionStore(resolved)
            _store_singletons[resolved] = store
        return store


def get_runtime_session_manager(root: str | Path | None = None) -> "RuntimeSessionManager":
    resolved = runtime_session_root(root)
    with _singleton_lock:
        manager = _manager_singletons.get(resolved)
        if manager is None:
            manager = RuntimeSessionManager(store=get_runtime_session_store(resolved))
            _manager_singletons[resolved] = manager
        return manager


def reset_runtime_session_singletons() -> None:
    with _singleton_lock:
        _store_singletons.clear()
        _manager_singletons.clear()


def resolve_session_display_name(session: Mapping[str, Any]) -> str:
    metadata = session.get("metadata", {})
    custom_name = metadata.get("custom_session_name") if isinstance(metadata, Mapping) else None
    if isinstance(custom_name, str):
        custom_name = custom_name.strip()
        if custom_name:
            return custom_name

    title = session.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    history = session.get("history", [])
    if isinstance(history, list):
        for msg in history:
            if isinstance(msg, Mapping) and msg.get("role") == "user":
                derived = _truncate(str(msg.get("content") or "New Chat"), 30)
                return derived if derived.strip() else "New Chat"

    return "New Chat"


class RuntimeSessionArtifacts:
    """Small artifact helper for non-history files such as chatlogs."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.storage_dir = runtime_session_root(root) / "artifacts"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    async def save_session(
        self,
        *,
        session_id: str,
        channel: str = "",
        messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        manager = get_runtime_session_manager(self.storage_dir.parent)
        await manager.merge_metadata(session_id, metadata or {})
        return True


class RuntimeSessionManager:
    """Async gateway API backed by a single runtime ``FileSessionStore``."""

    def __init__(
        self,
        *,
        store: FileSessionStore | None = None,
        root: str | Path | None = None,
        max_history: int | None = None,
        auto_save: bool | None = None,
        delete_file_context: Callable[[str], int | bool] | None = None,
    ) -> None:
        self.store = store or get_runtime_session_store(root)
        self.root = self.store.root
        self.max_history = max_history or DEFAULT_MAX_HISTORY
        self.auto_save = DEFAULT_AUTO_SAVE if auto_save is None else bool(auto_save)
        self.persistence_enabled = True
        self._initialized = False
        self._cleanup_task: Optional[asyncio.Task] = None
        self._delete_file_context = delete_file_context
        self.artifacts = RuntimeSessionArtifacts(self.root)

    @property
    def sessions(self) -> dict[str, dict[str, Any]]:
        return {session.session_id: self._session_to_legacy(session) for session in self.store.list_sessions()}

    async def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._initialized = True

    async def list_sessions(self) -> list[str]:
        sessions = sorted(
            self.store.list_sessions(),
            key=lambda session: (session.updated_at, session.session_id),
            reverse=True,
        )
        return [session.session_id for session in sessions]

    async def get_session(self, session_id: str) -> dict[str, Any]:
        session = self._ensure_session(session_id)
        return self._session_to_legacy(session)

    async def get_existing_session(self, session_id: str) -> Optional[dict[str, Any]]:
        try:
            return self._session_to_legacy(self.store.get_session(session_id))
        except KeyError:
            return None

    async def get_session_info(self, session_id: str) -> Optional[dict[str, Any]]:
        session = await self.get_existing_session(session_id)
        if session is None:
            return None
        session["history_count"] = len(session.get("history") or [])
        session["is_valid"] = True
        return session

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        session = await self.get_session(session_id)
        return list(session.get("history") or [])

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        wait_for_save: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> str:
        self._ensure_session(session_id)
        legacy_fields = deepcopy(dict(extra or {}))
        message_metadata = {
            "source": "gateway.facade",
            "legacy_fields": legacy_fields,
        }
        extra_metadata = legacy_fields.get("metadata")
        if isinstance(extra_metadata, Mapping):
            message_metadata.update(deepcopy(dict(extra_metadata)))

        message = self.store.append_message(
            session_id,
            role=_coerce_role(role),
            parts=[MessagePart.text_part(str(content or ""))] if content is not None else [],
            message_id=str(legacy_fields.get("id") or uuid.uuid4()),
            metadata=message_metadata,
            status=str(legacy_fields.get("status") or "complete"),
            completed_at=legacy_fields.get("completed_at") if isinstance(legacy_fields.get("completed_at"), str) else None,
        )
        self._trim_history_if_needed(session_id)
        return message.message_id

    async def clear_history(self, session_id: str) -> None:
        self._ensure_session(session_id)
        self.store.replace_history(session_id, [])

    async def replace_history(self, session_id: str, history: Iterable[Mapping[str, Any]]) -> None:
        self._ensure_session(session_id)
        self.store.replace_history(session_id, legacy_messages_to_runtime(session_id, history))

    async def delete_session(self, session_id: str) -> bool:
        removed = self.store.delete_session(session_id)
        chatlog_removed = self._delete_session_chatlog(session_id)
        file_context_removed = self._delete_session_file_context(session_id)
        return removed or chatlog_removed or file_context_removed

    async def rename_session(self, session_id: str, new_name: str) -> Optional[str]:
        normalized_name = str(new_name or "").strip()
        if not normalized_name:
            raise ValueError("Session name cannot be empty")
        if len(normalized_name) > 120:
            raise ValueError("Session name must be at most 120 characters")
        try:
            self.store.update_session(
                session_id,
                title=normalized_name,
                metadata={"custom_session_name": normalized_name},
            )
        except KeyError:
            return None
        return normalized_name

    async def edit_message(self, session_id: str, message_id: str, new_content: str) -> bool:
        messages = self._read_existing_history(session_id)
        edited = False
        for message in messages:
            if message.message_id != message_id:
                continue
            self._replace_message_content(message, new_content)
            edited = True
            break
        if edited:
            self.store.replace_history(session_id, messages)
        return edited

    async def delete_message(self, session_id: str, message_id: str) -> bool:
        messages = self._read_existing_history(session_id)
        updated = [message for message in messages if message.message_id != message_id]
        if len(updated) == len(messages):
            return False
        self.store.replace_history(session_id, updated)
        return True

    async def delete_messages_after(self, session_id: str, message_id: str) -> int:
        messages = self._read_existing_history(session_id)
        index = _message_index(messages, message_id)
        if index is None:
            return 0
        deleted_count = len(messages) - index - 1
        self.store.replace_history(session_id, messages[: index + 1])
        return deleted_count

    async def delete_messages_from(
        self,
        session_id: str,
        message_id: str,
        wait_for_save: bool = False,
    ) -> int:
        messages = self._read_existing_history(session_id)
        index = _message_index(messages, message_id)
        if index is None:
            return 0
        deleted_count = len(messages) - index
        self.store.replace_history(session_id, messages[:index])
        return deleted_count

    async def get_active_skill_session(self, session_id: str) -> Optional[dict[str, Any]]:
        session = await self.get_session(session_id)
        active = session.get("active_skill_session")
        return deepcopy(active) if isinstance(active, dict) else None

    async def set_active_skill_session(
        self,
        session_id: str,
        skill_session: Optional[dict[str, Any]],
    ) -> None:
        metadata = {"active_skill_session": deepcopy(skill_session)}
        await self.replace_metadata_keys(session_id, metadata)

    async def get_last_execution_id(self, session_id: str) -> Optional[str]:
        session = await self.get_session(session_id)
        value = session.get("metadata", {}).get("last_execution_id")
        return value.strip() if isinstance(value, str) and value.strip() else None

    async def set_last_execution_id(self, session_id: str, request_id: Optional[str]) -> None:
        if not session_id or not request_id:
            return
        await self.merge_metadata(session_id, {"last_execution_id": request_id})

    async def mark_runtime_running(self, session_id: str, *, request_id: str) -> None:
        if not session_id or not request_id:
            return
        await self.merge_metadata(
            session_id,
            {
                "last_execution_id": request_id,
                "last_runtime_status": "running",
                "last_runtime_updated_at": _now_iso(),
                "latest_event_type": "chat.started",
                "latest_event_state": "running",
                "completion_state": "running",
            },
        )

    async def get_context_state(self, session_id: str) -> Optional[dict[str, Any]]:
        session = await self.get_session(session_id)
        context_state = session.get("metadata", {}).get("context_state")
        return deepcopy(context_state) if isinstance(context_state, dict) else None

    async def set_context_state(self, session_id: str, context_state: dict[str, Any]) -> None:
        if not session_id or not isinstance(context_state, dict):
            return
        metadata: dict[str, Any] = {"context_state": deepcopy(context_state)}

        def set_if_present(key: str, value: Any) -> None:
            if value not in (None, ""):
                metadata[key] = value

        set_if_present("context_compaction_level", context_state.get("compaction_level"))
        set_if_present("context_objective_preview", _truncate(str(context_state.get("objective") or ""), 140))
        summary_preview = _truncate(str(context_state.get("summary") or ""), 180)
        set_if_present("context_summary_preview", summary_preview)
        set_if_present("context_next_step_preview", _truncate(str(context_state.get("next_step") or ""), 140))
        budget = context_state.get("budget") if isinstance(context_state.get("budget"), dict) else {}
        usage_percent = budget.get("prepared_usage_percent") or budget.get("usage_percent")
        estimated_tokens = budget.get("prepared_tokens") or budget.get("estimated_tokens")
        set_if_present("context_usage_percent", usage_percent)
        set_if_present("context_estimated_tokens", estimated_tokens)
        set_if_present("context_window_tokens", budget.get("context_window_tokens"))
        set_if_present("context_next_compaction_action", budget.get("next_compaction_action"))
        set_if_present("context_next_pruning_policy", budget.get("next_pruning_policy"))
        set_if_present("context_tokens_until_soft_threshold", budget.get("tokens_until_soft_threshold"))
        set_if_present("context_tokens_until_hard_threshold", budget.get("tokens_until_hard_threshold"))
        if context_state.get("compaction_level") == "full" and summary_preview:
            metadata["compaction_summary"] = summary_preview
        await self.replace_metadata_keys(
            session_id,
            metadata,
            remove_keys={
                "context_compaction_level",
                "context_objective_preview",
                "context_summary_preview",
                "context_next_step_preview",
                "context_usage_percent",
                "context_estimated_tokens",
                "context_window_tokens",
                "context_next_compaction_action",
                "context_next_pruning_policy",
                "context_tokens_until_soft_threshold",
                "context_tokens_until_hard_threshold",
                "compaction_summary",
            },
        )

    async def get_pending_delegations(self, session_id: str) -> list[dict[str, Any]]:
        session = await self.get_session(session_id)
        pending = session.get("metadata", {}).get("pending_delegations")
        return [deepcopy(item) for item in pending if isinstance(item, dict)] if isinstance(pending, list) else []

    async def add_pending_delegation(self, session_id: str, delegation_record: dict[str, Any]) -> None:
        if not session_id or not isinstance(delegation_record, dict):
            return
        session = await self.get_session(session_id)
        metadata = dict(session.get("metadata") or {})
        pending = metadata.get("pending_delegations")
        pending_items = [item for item in pending if isinstance(item, dict)] if isinstance(pending, list) else []
        delegation_id = delegation_record.get("delegation_id")
        if delegation_id:
            pending_items = [item for item in pending_items if item.get("delegation_id") != delegation_id]
        pending_items.append(deepcopy(delegation_record))
        await self.merge_metadata(session_id, {"pending_delegations": pending_items})

    async def complete_pending_delegation(
        self,
        session_id: str,
        delegation_id: str,
        *,
        status: str,
    ) -> None:
        if not session_id or not delegation_id:
            return
        session = await self.get_session(session_id)
        metadata = dict(session.get("metadata") or {})
        pending = metadata.get("pending_delegations")
        pending_items = [item for item in pending if isinstance(item, dict)] if isinstance(pending, list) else []
        remaining: list[dict[str, Any]] = []
        matched: dict[str, Any] | None = None
        for item in pending_items:
            if item.get("delegation_id") == delegation_id:
                matched = deepcopy(item)
            else:
                remaining.append(item)
        updates: dict[str, Any] = {"pending_delegations": remaining}
        if matched is not None:
            completed = metadata.get("completed_delegations")
            completed_items = [item for item in completed if isinstance(item, dict)] if isinstance(completed, list) else []
            matched["status"] = status
            matched["completed_at"] = _now_iso()
            completed_items.append(matched)
            updates["completed_delegations"] = completed_items[-50:]
        await self.merge_metadata(session_id, updates)

    async def merge_metadata(self, session_id: str, metadata: Mapping[str, Any]) -> None:
        self._ensure_session(session_id)
        self.store.update_session(session_id, metadata=deepcopy(dict(metadata)))

    async def replace_metadata_keys(
        self,
        session_id: str,
        metadata: Mapping[str, Any],
        *,
        remove_keys: Iterable[str] = (),
    ) -> None:
        session = self._ensure_session(session_id)
        updated = deepcopy(session.metadata)
        for key in remove_keys:
            updated.pop(key, None)
        updated.update(deepcopy(dict(metadata)))
        self.store.update_session(session_id, metadata=updated, replace_metadata=True)

    async def record_runtime_result(
        self,
        session_id: str,
        result: Any,
        *,
        request_id: str | None = None,
    ) -> None:
        session = self._ensure_session(session_id)
        metadata = deepcopy(session.metadata)
        if request_id:
            metadata["last_execution_id"] = request_id
        runtime_status = getattr(result, "status", None)
        metadata["last_runtime_status"] = runtime_status
        metadata["last_runtime_updated_at"] = _now_iso()
        normalized_status = str(runtime_status or "").strip().lower()
        if normalized_status in {"success", "completed", "complete", "ok"}:
            metadata["latest_event_type"] = "chat.completed"
            metadata["latest_event_state"] = "success"
            metadata["completion_state"] = "completed"
        elif normalized_status in {"error", "failed", "failure"}:
            metadata["latest_event_type"] = "chat.failed"
            metadata["latest_event_state"] = "error"
            metadata["completion_state"] = "error"
        elif normalized_status in {"cancelled", "canceled"}:
            metadata["latest_event_type"] = "chat.cancelled"
            metadata["latest_event_state"] = "cancelled"
            metadata["completion_state"] = "cancelled"
        pending_permission = getattr(result, "pending_permission_request", None)
        pending_question = getattr(result, "pending_question_request", None)
        if pending_permission is not None:
            metadata["pending_permission_request"] = deepcopy(pending_permission)
        else:
            metadata.pop("pending_permission_request", None)
        if pending_question is not None:
            metadata["pending_question_request"] = deepcopy(pending_question)
        else:
            metadata.pop("pending_question_request", None)
        pending_tool_calls = _pending_tool_call_payloads(self.store.read_history(session_id))
        if pending_tool_calls:
            metadata["pending_tool_calls"] = pending_tool_calls
        else:
            metadata.pop("pending_tool_calls", None)
        self.store.update_session(session_id, metadata=metadata, replace_metadata=True)

    async def recover_session_state(self, session_id: str) -> dict[str, Any]:
        pipeline_payload = await _recover_session_via_runtime_pipeline(session_id, self)
        if pipeline_payload is not None:
            return pipeline_payload

        warnings: list[str] = []
        try:
            session = self.store.get_session(session_id)
            recovered = True
        except KeyError:
            session = self.store.create_session(session_id=session_id)
            recovered = False
            warnings.append("Session did not exist; created an empty EFP runtime session.")

        metadata = deepcopy(session.metadata)
        pending_tool_calls = _pending_tool_call_payloads(session.messages)
        runtime_state = _runtime_state_from_metadata(
            metadata,
            pending_tool_calls=pending_tool_calls,
        )
        reconstructed_state = _reconstructed_state_from_session(
            session,
            pending_tool_calls=pending_tool_calls,
            runtime_state=runtime_state,
        )
        recovery_context_message = _recovery_context_message(
            session,
            runtime_state=runtime_state,
            reconstructed_state=reconstructed_state,
        )
        if recovery_context_message is not None:
            reconstructed_state["recovery_context_message"] = recovery_context_message

        active_skill = metadata.get("active_skill_session")
        return {
            "session_id": session.session_id,
            "recovered": recovered,
            "snapshot_version": metadata.get("snapshot_version", 1),
            "active_skill_session": (
                deepcopy(active_skill) if isinstance(active_skill, dict) else None
            ),
            "last_execution_id": metadata.get("last_execution_id"),
            "runtime_state": runtime_state,
            "reconstructed_state": reconstructed_state,
            "warnings": warnings,
            "runtime_events": _runtime_events_from_metadata(metadata),
            "metadata": metadata,
            "recovery_context_message": recovery_context_message,
        }

    async def save_all(self) -> None:
        return None

    async def shutdown(self) -> None:
        self._initialized = False

    def chatlog_file(self, session_id: str) -> Path:
        return self.artifacts.storage_dir / "chatlogs" / f"{session_id}.json"

    def _ensure_session(self, session_id: str) -> Session:
        try:
            return self.store.get_session(session_id)
        except KeyError:
            return self.store.create_session(session_id=session_id)

    def _read_existing_history(self, session_id: str) -> list[Message]:
        self._ensure_session(session_id)
        return self.store.read_history(session_id)

    def _trim_history_if_needed(self, session_id: str) -> None:
        if self.max_history <= 0:
            return
        history = self.store.read_history(session_id)
        max_messages = self.max_history * 2
        if len(history) > max_messages:
            self.store.replace_history(session_id, history[-max_messages:])

    def _replace_message_content(self, message: Message, new_content: str) -> None:
        for part in message.parts:
            if part.type is MessagePartType.TEXT:
                part.text = new_content
                return
            if part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None:
                part.tool_result.content = new_content
                part.tool_result.output = new_content
                return
            if part.type is MessagePartType.COMPACTION and part.compaction is not None:
                part.compaction.summary = new_content
                part.text = new_content
                return
        message.parts.append(MessagePart.text_part(new_content))

    def _delete_session_chatlog(self, session_id: str) -> bool:
        try:
            path = self.chatlog_file(session_id)
            if path.exists():
                path.unlink()
                return True
        except Exception:
            logger.debug("Failed to delete runtime chatlog", exc_info=True)
        return False

    def _delete_session_file_context(self, session_id: str) -> bool:
        delete_file_context = self._delete_file_context or _resolve_file_context_delete()
        if delete_file_context is None:
            return False
        try:
            deleted = delete_file_context(session_id)
            if isinstance(deleted, bool):
                return deleted
            return deleted > 0
        except Exception:
            logger.debug("Failed to delete file context for runtime session", exc_info=True)
            return False

    def _session_to_legacy(self, session: Session) -> dict[str, Any]:
        metadata = deepcopy(session.metadata)
        active_skill = metadata.get("active_skill_session")
        return {
            "session_id": session.session_id,
            "title": session.title,
            "history": [_message_to_legacy(message) for message in session.messages],
            "channel": str(metadata.get("channel") or ""),
            "metadata": metadata,
            "active_skill_session": deepcopy(active_skill) if isinstance(active_skill, dict) else None,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "_persisted": True,
            "_runtime_store": "runtime_file",
        }


def _message_to_legacy(message: Message) -> dict[str, Any]:
    legacy_fields = message.metadata.get("legacy_fields")
    item: dict[str, Any] = deepcopy(legacy_fields) if isinstance(legacy_fields, Mapping) else {}
    content = _message_content(message)
    metadata = {key: deepcopy(value) for key, value in message.metadata.items() if key != "legacy_fields"}
    if isinstance(item.get("metadata"), Mapping):
        merged_metadata = deepcopy(metadata)
        merged_metadata.update(deepcopy(dict(item["metadata"])))
        item["metadata"] = merged_metadata
    elif metadata:
        item["metadata"] = metadata

    item.update(
        {
            "id": message.message_id,
            "role": message.role.value,
            "content": content,
            "timestamp": message.created_at,
            "created_at": message.created_at,
            "status": message.status,
            "parts": [_part_to_legacy(part) for part in message.parts],
        }
    )
    if message.completed_at:
        item["completed_at"] = message.completed_at
    if message.parent_message_id:
        item["parent_message_id"] = message.parent_message_id
    if message.usage:
        item["usage"] = deepcopy(message.usage)
    tool_calls = [_tool_call_to_legacy(part.tool_call) for part in message.parts if part.tool_call is not None]
    if tool_calls:
        item["tool_calls"] = tool_calls
    tool_results = [part.tool_result for part in message.parts if part.tool_result is not None]
    if tool_results:
        item["tool_call_id"] = tool_results[0].call_id
        item["tool_name"] = tool_results[0].tool_name
    if any(part.type is MessagePartType.COMPACTION for part in message.parts):
        item["type"] = "compaction_summary"
    return item


def _part_to_legacy(part: MessagePart) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": part.part_id,
        "type": part.type.value,
        "created_at": part.created_at,
        "metadata": deepcopy(part.metadata),
    }
    if part.text is not None:
        item["text"] = part.text
    if part.reasoning is not None:
        item["reasoning"] = part.reasoning
    if part.tool_call is not None:
        item["tool_call"] = _tool_call_to_legacy(part.tool_call)
    if part.tool_result is not None:
        item["tool_result"] = part.tool_result.to_dict()
    if part.compaction is not None:
        item["compaction"] = _compaction_to_legacy(part.compaction)
    if part.attachment is not None:
        item["attachment"] = {
            "id": part.attachment.attachment_id,
            "mime_type": part.attachment.mime_type,
            "filename": part.attachment.filename,
            "url": part.attachment.url,
            "text_ref": part.attachment.text_ref,
            "metadata": deepcopy(part.attachment.metadata),
        }
    return item


def _message_content(message: Message) -> str:
    chunks: list[str] = []
    for part in message.parts:
        if part.type is MessagePartType.TEXT and part.text:
            chunks.append(part.text)
        elif part.type is MessagePartType.REASONING and part.reasoning and not chunks:
            chunks.append(part.reasoning)
        elif part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None:
            chunks.append(part.tool_result.content)
        elif part.type is MessagePartType.COMPACTION and part.compaction is not None:
            chunks.append(part.compaction.summary)
        elif part.type is MessagePartType.ERROR and part.text:
            chunks.append(part.text)
    return "\n".join(chunk for chunk in chunks if chunk is not None)


def _tool_call_to_legacy(tool_call: ToolCall) -> dict[str, Any]:
    return {
        "id": tool_call.call_id,
        "type": tool_call.call_type,
        "function": {
            "name": tool_call.tool_name,
            "arguments": tool_call.arguments_text,
        },
        "name": tool_call.tool_name,
        "arguments": deepcopy(tool_call.arguments),
        "status": tool_call.status,
        "metadata": deepcopy(tool_call.metadata),
        "created_at": tool_call.created_at,
    }


def _compaction_to_legacy(compaction: CompactionPart) -> dict[str, Any]:
    return {
        "summary": compaction.summary,
        "source_message_ids": list(compaction.source_message_ids),
        "auto": compaction.auto,
        "overflow": compaction.overflow,
        "tail_start_message_id": compaction.tail_start_message_id,
        "original_part_count": compaction.original_part_count,
        "original_message_count": compaction.original_message_count,
        "tool_pair_count": compaction.tool_pair_count,
        "metadata": deepcopy(compaction.metadata),
    }


def _pending_tool_call_payloads(history: Iterable[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    result_ids = {
        part.tool_result.call_id
        for message in history
        for part in message.parts
        if part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None
    }
    for message in history:
        if message.role is not MessageRole.ASSISTANT:
            continue
        for part in message.parts:
            if part.type is MessagePartType.TOOL_CALL and part.tool_call is not None and part.tool_call.call_id not in result_ids:
                result.append(
                    {
                        "message_id": message.message_id,
                        "part_id": part.part_id,
                        "call_id": part.tool_call.call_id,
                        "tool_name": part.tool_call.tool_name,
                        "arguments": deepcopy(part.tool_call.arguments),
                    }
                )
    return result


def _runtime_state_from_metadata(
    metadata: Mapping[str, Any],
    *,
    pending_tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    pending_permission = metadata.get("pending_permission_request")
    pending_question = metadata.get("pending_question_request")
    status = metadata.get("last_runtime_status")
    if not isinstance(status, str) or not status:
        if pending_permission is not None:
            status = "waiting_for_permission"
        elif pending_question is not None:
            status = "waiting_for_question"
        elif pending_tool_calls:
            status = "pending_tool_calls"
        else:
            status = "unknown"
    return {
        "status": status,
        "pending_permission_request": deepcopy(pending_permission),
        "pending_question_request": deepcopy(pending_question),
        "pending_tool_calls": deepcopy(pending_tool_calls),
    }


def _reconstructed_state_from_session(
    session: Session,
    *,
    pending_tool_calls: list[dict[str, Any]],
    runtime_state: Mapping[str, Any],
) -> dict[str, Any]:
    latest_user_message = _latest_message(session.messages, MessageRole.USER)
    latest_assistant_message = _latest_message(session.messages, MessageRole.ASSISTANT)
    return {
        "message_count": len(session.messages),
        "last_message_id": (
            session.messages[-1].message_id if session.messages else None
        ),
        "latest_user_message": (
            _message_to_legacy(latest_user_message)
            if latest_user_message is not None
            else None
        ),
        "latest_assistant_message": (
            _message_to_legacy(latest_assistant_message)
            if latest_assistant_message is not None
            else None
        ),
        "pending_tool_calls": deepcopy(pending_tool_calls),
        "has_pending_tool_calls": bool(pending_tool_calls),
        "has_pending_permission": runtime_state.get("pending_permission_request")
        is not None,
        "has_pending_question": runtime_state.get("pending_question_request")
        is not None,
    }


def _latest_message(
    messages: Iterable[Message],
    role: MessageRole,
) -> Message | None:
    for message in reversed(list(messages)):
        if message.role is role:
            return message
    return None


def _runtime_events_from_metadata(metadata: Mapping[str, Any]) -> list[Any]:
    runtime_events = metadata.get("runtime_events")
    if isinstance(runtime_events, list):
        return deepcopy(runtime_events)
    return []


async def _recover_session_via_runtime_pipeline(
    session_id: str,
    manager: RuntimeSessionManager,
) -> dict[str, Any] | None:
    try:
        module = importlib.import_module("src." + "runtime.recovery_pipeline")
        get_recovery_pipeline = getattr(module, "get_recovery_pipeline")
        result = await get_recovery_pipeline().hydrate_session_state(session_id)
    except Exception:
        return None
    if _is_empty_pipeline_miss(result) or _is_pipeline_stale_for_manager(
        result,
        manager,
        session_id,
    ):
        return None
    return _hydration_result_to_payload(result)


def _is_empty_pipeline_miss(result: Any) -> bool:
    warnings = list(getattr(result, "warnings", []) or [])
    return (
        getattr(result, "recovered", False) is False
        and warnings == ["session_not_found"]
        and not getattr(result, "metadata", None)
        and not getattr(result, "runtime_state", None)
        and not getattr(result, "reconstructed_state", None)
    )


def _is_pipeline_stale_for_manager(
    result: Any,
    manager: RuntimeSessionManager,
    session_id: str,
) -> bool:
    try:
        local_session = manager.store.get_session(session_id)
    except KeyError:
        return False
    result_metadata = getattr(result, "metadata", None)
    result_state = getattr(result, "reconstructed_state", None)
    if local_session.metadata and not result_metadata:
        return True
    if local_session.messages and not (
        isinstance(result_state, Mapping)
        and result_state.get("message_count")
    ):
        return True
    return False


def _hydration_result_to_payload(result: Any) -> dict[str, Any]:
    reconstructed_state = deepcopy(dict(getattr(result, "reconstructed_state", {}) or {}))
    recovery_context_message = reconstructed_state.get("recovery_context_message")
    return {
        "session_id": getattr(result, "session_id", None),
        "recovered": bool(getattr(result, "recovered", False)),
        "snapshot_version": getattr(result, "snapshot_version", None),
        "active_skill_session": deepcopy(getattr(result, "active_skill_session", None)),
        "last_execution_id": getattr(result, "last_execution_id", None),
        "runtime_state": deepcopy(dict(getattr(result, "runtime_state", {}) or {})),
        "reconstructed_state": reconstructed_state,
        "warnings": list(getattr(result, "warnings", []) or []),
        "runtime_events": deepcopy(list(getattr(result, "runtime_events", []) or [])),
        "metadata": deepcopy(dict(getattr(result, "metadata", {}) or {})),
        "recovery_context_message": recovery_context_message,
    }


def _resolve_file_context_delete() -> Callable[[str], int | bool] | None:
    try:
        module = importlib.import_module("src." + "hooks.file_context.storage")
        file_context_storage = getattr(module, "storage")
        delete_session = getattr(file_context_storage, "delete_session", None)
        return delete_session if callable(delete_session) else None
    except Exception:
        return None


def _recovery_context_message(
    session: Session,
    *,
    runtime_state: Mapping[str, Any],
    reconstructed_state: Mapping[str, Any],
) -> str | None:
    if not session.messages:
        return None
    status = runtime_state.get("status") or "unknown"
    pending_count = len(runtime_state.get("pending_tool_calls") or [])
    return (
        "EFP runtime session recovery: "
        f"status={status}, messages={reconstructed_state.get('message_count', 0)}, "
        f"pending_tool_calls={pending_count}."
    )


def _coerce_role(role: str) -> MessageRole:
    try:
        return MessageRole(str(role))
    except ValueError:
        return MessageRole.SYSTEM


def _message_index(messages: list[Message], message_id: str) -> int | None:
    for index, message in enumerate(messages):
        if message.message_id == message_id:
            return index
    return None


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def legacy_messages_to_runtime(session_id: str, messages: Iterable[Mapping[str, Any]]) -> list[Message]:
    runtime_messages: list[Message] = []
    for item in messages:
        role = _coerce_role(str(item.get("role") or "user"))
        content = str(item.get("content") or "")
        metadata = deepcopy(dict(item.get("metadata") or {})) if isinstance(item.get("metadata"), Mapping) else {}
        legacy_fields = {
            key: deepcopy(value)
            for key, value in item.items()
            if key not in {"role", "content", "timestamp", "created_at", "metadata", "parts"}
        }
        message = Message(
            role=role,
            session_id=session_id,
            message_id=str(item.get("id") or item.get("message_id") or uuid.uuid4()),
            metadata={**metadata, "legacy_fields": legacy_fields},
            status=str(item.get("status") or "complete"),
            created_at=str(item.get("created_at") or item.get("timestamp") or _now_iso()),
            completed_at=item.get("completed_at") if isinstance(item.get("completed_at"), str) else None,
        )
        if content:
            message.append_part(MessagePart.text_part(content))
        runtime_messages.append(message)
    return runtime_messages


runtime_session_manager = get_runtime_session_manager()


__all__ = [
    "DEFAULT_AUTO_SAVE",
    "DEFAULT_MAX_HISTORY",
    "JIRA_SESSION_PREFIX",
    "RuntimeSessionArtifacts",
    "RuntimeSessionManager",
    "get_runtime_session_manager",
    "get_runtime_session_store",
    "legacy_messages_to_runtime",
    "reset_runtime_session_singletons",
    "resolve_session_display_name",
    "runtime_session_manager",
    "runtime_session_root",
]
