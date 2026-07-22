"""Session revert and unrevert helpers for EFP runtime."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..types import utc_now_iso
from ..workspace_snapshots import WorkspaceSnapshotStore
from .models import Message, MessagePart, Session
from .protocol import SessionStore
from .summary import summarize_session_diffs


@dataclass(frozen=True)
class SessionRevertRunRecord:
    """Pre-run state needed to publish a later session revert target."""

    session_id: str
    run_id: str
    source: str
    message_ids: tuple[str, ...]
    workspace_snapshot_id: str | None
    created_at: str


def prepare_session_revert_record(
    *,
    store: SessionStore,
    session_id: str,
    run_id: str,
    source: str,
    workspace_snapshot_store: WorkspaceSnapshotStore | None = None,
    enable_workspace_snapshot: bool = True,
) -> SessionRevertRunRecord:
    """Capture the session and optional workspace state before a run starts."""

    try:
        session = store.get_session(session_id)
    except KeyError:
        session = store.create_session(session_id=session_id)

    workspace_snapshot_id = None
    if enable_workspace_snapshot and workspace_snapshot_store is not None:
        snapshot = workspace_snapshot_store.create_snapshot(
            label=f"session:{session_id}:{source}:before",
            metadata={
                "session_id": session_id,
                "run_id": run_id,
                "source": source,
                "purpose": "session_revert",
            },
        )
        workspace_snapshot_id = snapshot.snapshot_id

    return SessionRevertRunRecord(
        session_id=session_id,
        run_id=run_id,
        source=source,
        message_ids=tuple(message.message_id for message in session.messages),
        workspace_snapshot_id=workspace_snapshot_id,
        created_at=utc_now_iso(),
    )


def finalize_session_revert_record(
    *,
    store: SessionStore,
    record: SessionRevertRunRecord,
    status: str,
) -> dict[str, Any]:
    """Persist the revert target and diff summary after a run finishes."""

    history = store.read_history(record.session_id)
    target_message_id = _first_new_message_id(history, set(record.message_ids))
    summary = (
        summarize_session_diffs(history, message_id=target_message_id)
        if target_message_id is not None
        else summarize_session_diffs([])
    )
    now = utc_now_iso()
    revert_metadata = {
        "active": False,
        "message_id": target_message_id,
        "workspace_snapshot_id": record.workspace_snapshot_id,
        "run_id": record.run_id,
        "source": record.source,
        "created_at": record.created_at,
        "updated_at": now,
        "status": status,
        "summary": summary,
    }
    store.update_session(
        record.session_id,
        metadata={"revert": revert_metadata, "summary": summary},
    )
    return revert_metadata


def revert_session_state(
    *,
    store: SessionStore,
    session_id: str,
    workspace_snapshot_store: WorkspaceSnapshotStore | None = None,
    message_id: str | None = None,
    part_id: str | None = None,
    delete_added: bool = True,
) -> Session:
    """Revert a session to a message or part boundary."""

    session = store.get_session(session_id)
    history = store.read_history(session_id)
    target_message_id, target_part_id = _resolve_revert_target(
        session.messages,
        metadata=session.metadata,
        message_id=message_id,
        part_id=part_id,
    )
    summary = summarize_session_diffs(history, message_id=target_message_id)
    checkpoint = store.create_checkpoint(
        session_id,
        label="session_revert_history",
        metadata={
            "session_id": session_id,
            "message_id": target_message_id,
            "part_id": target_part_id,
            "purpose": "session_revert",
        },
    )

    current_revert = _mapping_or_empty(session.metadata.get("revert"))
    workspace_snapshot_id = _string_or_none(
        current_revert.get("workspace_snapshot_id")
    )
    unrevert_snapshot_id = None
    if workspace_snapshot_id is not None:
        if workspace_snapshot_store is None:
            raise TypeError("workspace snapshots require workspace_root")
        with workspace_snapshot_store.protect_snapshot(workspace_snapshot_id):
            unrevert_snapshot = workspace_snapshot_store.create_snapshot(
                label=f"session:{session_id}:unrevert",
                metadata={
                    "session_id": session_id,
                    "history_checkpoint_id": checkpoint.checkpoint_id,
                    "purpose": "session_unrevert",
                },
            )
            unrevert_snapshot_id = unrevert_snapshot.snapshot_id
            workspace_snapshot_store.restore_snapshot(
                workspace_snapshot_id,
                delete_added=delete_added,
            )

    trimmed_history, removed_counts = trim_session_history(
        history,
        message_id=target_message_id,
        part_id=target_part_id,
    )
    store.replace_history(session_id, trimmed_history)

    now = utc_now_iso()
    metadata = deepcopy(store.get_session(session_id).metadata)
    revert_metadata = deepcopy(current_revert)
    revert_metadata.update(
        {
            "active": True,
            "message_id": target_message_id,
            "part_id": target_part_id,
            "history_checkpoint_id": checkpoint.checkpoint_id,
            "workspace_snapshot_id": workspace_snapshot_id,
            "unrevert_snapshot_id": unrevert_snapshot_id,
            "delete_added": bool(delete_added),
            "removed_message_count": removed_counts["messages"],
            "removed_part_count": removed_counts["parts"],
            "summary": summary,
            "status": "reverted",
            "reverted_at": now,
            "updated_at": now,
        }
    )
    metadata["revert"] = revert_metadata
    metadata["summary"] = summary
    return store.update_session(session_id, metadata=metadata, replace_metadata=True)


def unrevert_session_state(
    *,
    store: SessionStore,
    session_id: str,
    workspace_snapshot_store: WorkspaceSnapshotStore | None = None,
    delete_added: bool = True,
) -> Session:
    """Restore a session history and workspace from an active revert."""

    session = store.get_session(session_id)
    active_revert = _mapping_or_empty(session.metadata.get("revert"))
    if active_revert.get("active") is not True:
        raise ValueError("session has no active revert")

    checkpoint_id = _string_or_none(active_revert.get("history_checkpoint_id"))
    if checkpoint_id is None:
        raise ValueError("active revert has no history checkpoint")

    store.restore_checkpoint(session_id, checkpoint_id)

    unrevert_snapshot_id = _string_or_none(active_revert.get("unrevert_snapshot_id"))
    if unrevert_snapshot_id is not None:
        if workspace_snapshot_store is None:
            raise TypeError("workspace snapshots require workspace_root")
        workspace_snapshot_store.restore_snapshot(
            unrevert_snapshot_id,
            delete_added=delete_added,
        )

    now = utc_now_iso()
    metadata = deepcopy(store.get_session(session_id).metadata)
    revert_metadata = deepcopy(active_revert)
    revert_metadata.update(
        {
            "active": False,
            "delete_added": bool(delete_added),
            "status": "unreverted",
            "unreverted_at": now,
            "updated_at": now,
        }
    )
    metadata["revert"] = revert_metadata
    if "summary" not in metadata and isinstance(active_revert.get("summary"), Mapping):
        metadata["summary"] = deepcopy(dict(active_revert["summary"]))
    return store.update_session(session_id, metadata=metadata, replace_metadata=True)


def trim_session_history(
    messages: list[Message],
    *,
    message_id: str,
    part_id: str | None = None,
) -> tuple[list[Message], dict[str, int]]:
    """Return history with a message or part range removed."""

    index = _message_index(messages, message_id)
    if part_id is None:
        removed = messages[index:]
        return (
            deepcopy(messages[:index]),
            {
                "messages": len(removed),
                "parts": sum(len(message.parts) for message in removed),
            },
        )

    target = messages[index]
    part_index = _part_index(target.parts, part_id)
    trimmed = deepcopy(messages[: index + 1])
    trimmed[index].parts = deepcopy(target.parts[:part_index])
    removed_parts = len(target.parts) - part_index
    removed_parts += sum(len(message.parts) for message in messages[index + 1 :])
    return (
        trimmed,
        {
            "messages": len(messages) - index - 1,
            "parts": removed_parts,
        },
    )


def _resolve_revert_target(
    messages: list[Message],
    *,
    metadata: Mapping[str, Any],
    message_id: str | None,
    part_id: str | None,
) -> tuple[str, str | None]:
    if part_id is not None and message_id is None:
        for message in messages:
            if any(part.part_id == part_id for part in message.parts):
                return message.message_id, part_id
        raise KeyError(f"unknown part: {part_id}")

    target_message_id = message_id or _metadata_revert_message_id(metadata)
    if target_message_id is None:
        target_message_id = _last_message_id(messages, role="user")
    if target_message_id is None:
        target_message_id = _last_message_id(messages)
    if target_message_id is None:
        raise ValueError("session has no messages to revert")

    message = messages[_message_index(messages, target_message_id)]
    if part_id is not None:
        _part_index(message.parts, part_id)
    return target_message_id, part_id


def _metadata_revert_message_id(metadata: Mapping[str, Any]) -> str | None:
    revert = metadata.get("revert")
    if not isinstance(revert, Mapping):
        return None
    return _string_or_none(revert.get("message_id"))


def _first_new_message_id(
    messages: list[Message],
    existing_message_ids: set[str],
) -> str | None:
    for message in messages:
        if message.message_id not in existing_message_ids:
            return message.message_id
    return None


def _message_index(messages: list[Message], message_id: str) -> int:
    for index, message in enumerate(messages):
        if message.message_id == message_id:
            return index
    raise KeyError(f"unknown message: {message_id}")


def _part_index(parts: list[MessagePart], part_id: str) -> int:
    for index, part in enumerate(parts):
        if part.part_id == part_id:
            return index
    raise KeyError(f"unknown part: {part_id}")


def _last_message_id(messages: list[Message], role: str | None = None) -> str | None:
    for message in reversed(messages):
        if role is None or message.role.value == role:
            return message.message_id
    return None


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


__all__ = [
    "SessionRevertRunRecord",
    "finalize_session_revert_record",
    "prepare_session_revert_record",
    "revert_session_state",
    "trim_session_history",
    "unrevert_session_state",
]
