"""Fork metadata helpers for Runtime v2 sessions."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


RUNTIME_FORK_METADATA_KEYS = frozenset(
    {
        "revert",
        "summary",
        "last_execution_id",
        "last_runtime_status",
        "last_runtime_updated_at",
        "pending_permission_request",
        "pending_question_request",
        "pending_tool_calls",
    }
)


def fork_session_metadata(
    source_metadata: Mapping[str, Any],
    *,
    parent_session_id: str,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Return source metadata suitable for a forked session."""

    metadata = {
        str(key): deepcopy(value)
        for key, value in source_metadata.items()
        if key not in RUNTIME_FORK_METADATA_KEYS
    }
    metadata["parent_session_id"] = parent_session_id
    if message_id is not None:
        metadata["forked_from_message_id"] = message_id
    return metadata


__all__ = ["RUNTIME_FORK_METADATA_KEYS", "fork_session_metadata"]
