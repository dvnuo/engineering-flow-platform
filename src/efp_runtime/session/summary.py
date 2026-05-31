"""Session diff collection and summary helpers for EFP runtime."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import json
from typing import Any

from .models import Message, MessagePart, MessagePartType


def collect_session_file_diffs(
    messages: Iterable[Message],
    message_id: str | None = None,
) -> list[dict[str, Any]]:
    """Collect normalized file diffs from tool result parts in session history."""

    history = list(messages)
    if message_id is not None:
        history = history[_message_index(history, message_id) :]

    diffs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in history:
        for part in message.parts:
            if part.type is not MessagePartType.TOOL_RESULT:
                continue
            if part.tool_result is None:
                continue
            for raw_diff in _iter_tool_result_file_diffs(part):
                normalized = _normalize_file_diff(
                    raw_diff,
                    source_message_id=message.message_id,
                    source_part_id=part.part_id,
                    source_tool_call_id=part.tool_result.call_id,
                )
                key = _dedupe_key(normalized)
                if key in seen:
                    continue
                seen.add(key)
                diffs.append(normalized)
    return diffs


def summarize_session_diffs(
    messages: Iterable[Message],
    message_id: str | None = None,
) -> dict[str, Any]:
    """Return aggregate file diff stats for a session history range."""

    diffs = collect_session_file_diffs(messages, message_id=message_id)
    files = sorted(
        {
            path
            for diff in diffs
            for path in (diff.get("old_path"), diff.get("path"))
            if isinstance(path, str) and path
        }
    )
    return {
        "message_id": message_id,
        "diff_count": len(diffs),
        "file_count": len(files),
        "files": files,
        "additions": sum(_safe_int(diff.get("additions")) for diff in diffs),
        "deletions": sum(_safe_int(diff.get("deletions")) for diff in diffs),
        "diffs": diffs,
    }


def _iter_tool_result_file_diffs(part: MessagePart) -> Iterable[Mapping[str, Any]]:
    result = part.tool_result
    if result is None:
        return []

    candidates: list[Mapping[str, Any]] = []
    for container in (result.metadata, result.output):
        if not isinstance(container, Mapping):
            continue
        filediff = container.get("filediff")
        if isinstance(filediff, Mapping):
            candidates.append(filediff)
        filediffs = container.get("filediffs")
        if isinstance(filediffs, list):
            candidates.extend(item for item in filediffs if isinstance(item, Mapping))
    return candidates


def _normalize_file_diff(
    diff: Mapping[str, Any],
    *,
    source_message_id: str,
    source_part_id: str,
    source_tool_call_id: str,
) -> dict[str, Any]:
    path = _optional_string(diff.get("path") or diff.get("filePath"))
    old_path = _optional_string(diff.get("old_path") or diff.get("oldPath"))
    if old_path is None and path is not None:
        old_path = path
    metadata = _diff_metadata(diff)
    return {
        "path": path,
        "old_path": old_path,
        "additions": _safe_int(diff.get("additions")),
        "deletions": _safe_int(diff.get("deletions")),
        "patch": _optional_string(diff.get("patch")),
        "source_message_id": source_message_id,
        "source_part_id": source_part_id,
        "source_tool_call_id": source_tool_call_id,
        "metadata": metadata,
    }


def _message_index(messages: list[Message], message_id: str) -> int:
    for index, message in enumerate(messages):
        if message.message_id == message_id:
            return index
    raise KeyError(f"unknown message: {message_id}")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _diff_metadata(diff: Mapping[str, Any]) -> dict[str, Any]:
    known_keys = {
        "path",
        "filePath",
        "old_path",
        "oldPath",
        "additions",
        "deletions",
        "patch",
        "metadata",
    }
    metadata = {
        str(key): deepcopy(value)
        for key, value in diff.items()
        if key not in known_keys
    }
    raw_metadata = diff.get("metadata")
    if isinstance(raw_metadata, Mapping):
        metadata.update(deepcopy(dict(raw_metadata)))
    return metadata


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        integer = int(value)
    except (TypeError, ValueError):
        return 0
    return integer if integer >= 0 else 0


def _dedupe_key(diff: Mapping[str, Any]) -> str:
    payload = {
        "path": diff.get("path"),
        "old_path": diff.get("old_path"),
        "additions": diff.get("additions"),
        "deletions": diff.get("deletions"),
        "patch": diff.get("patch"),
        "source_message_id": diff.get("source_message_id"),
        "source_part_id": diff.get("source_part_id"),
        "source_tool_call_id": diff.get("source_tool_call_id"),
        "metadata": diff.get("metadata"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "collect_session_file_diffs",
    "summarize_session_diffs",
]
