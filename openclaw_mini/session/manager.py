"""Session management for OpenClaw Mini."""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

# Session ID prefix for Discord channels
DISCORD_SESSION_PREFIX = "discord:"


class SessionManager:
    """Simple in-memory session manager."""

    def __init__(self, max_history: int = 5):
        self.max_history = max_history
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get or create a session."""
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "history": [],
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
        return self.sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Add a message to the session history."""
        session = self.get_session(session_id)
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
        }
        session["history"].append(message)

        # Keep only recent history
        if len(session["history"]) > self.max_history * 2:
            session["history"] = session["history"][-self.max_history * 2 :]

        session["updated_at"] = datetime.utcnow().isoformat()

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """Get conversation history for a session."""
        session = self.get_session(session_id)
        return session["history"]

    def clear_history(self, session_id: str) -> None:
        """Clear session history."""
        if session_id in self.sessions:
            self.sessions[session_id]["history"] = []
            self.sessions[session_id]["updated_at"] = datetime.utcnow().isoformat()

    def list_sessions(self) -> List[str]:
        """List all active session IDs."""
        return list(self.sessions.keys())

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session metadata (without full history)."""
        if session_id not in self.sessions:
            return None
        session = self.sessions[session_id].copy()
        # Truncate history for info
        session["history_count"] = len(session.get("history", []))
        return session


# Global session manager instance
session_manager = SessionManager()
