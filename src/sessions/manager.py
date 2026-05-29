"""Compatibility wrapper for the Runtime v2 file-backed session facade."""

from __future__ import annotations

from src.efp_runtime.session.gateway_facade import (
    DEFAULT_AUTO_SAVE,
    DEFAULT_MAX_HISTORY,
    JIRA_SESSION_PREFIX,
    RuntimeV2SessionManager,
    get_runtime_v2_session_manager,
    resolve_session_display_name,
    runtime_v2_session_manager,
)


SessionManager = RuntimeV2SessionManager
session_manager = runtime_v2_session_manager


def get_session_manager() -> RuntimeV2SessionManager:
    return get_runtime_v2_session_manager()


__all__ = [
    "DEFAULT_AUTO_SAVE",
    "DEFAULT_MAX_HISTORY",
    "JIRA_SESSION_PREFIX",
    "RuntimeV2SessionManager",
    "SessionManager",
    "get_session_manager",
    "resolve_session_display_name",
    "session_manager",
]
