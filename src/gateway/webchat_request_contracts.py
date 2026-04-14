"""Lightweight request-id contracts shared by WebChat handlers and tests."""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional


def extract_trusted_client_request_id(
    is_trusted_portal_request: bool,
    data: Mapping[str, Any],
) -> Optional[str]:
    """Return accepted client request id for trusted portal requests only."""
    if not is_trusted_portal_request:
        return None
    candidate = data.get("client_request_id")
    if candidate is None:
        candidate = data.get("request_id")
    if not isinstance(candidate, str):
        return None
    cleaned = candidate.strip()
    if not cleaned or len(cleaned) > 128:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_:-]+", cleaned):
        return None
    return cleaned


def build_stream_start_event_payload(session_id: str, request_id: str) -> Dict[str, str]:
    """Return the stable start-event payload for stream chat responses."""
    return {
        "session_id": session_id,
        "request_id": request_id,
    }
