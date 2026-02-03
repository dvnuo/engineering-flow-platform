"""Session management for OpenClaw Mini with persistence."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from session.persistence import session_store

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run coroutine, handling both sync and async contexts.
    
    In Python 3.9+, asyncio.run() cannot be called from a running event loop.
    This helper detects the context and uses the appropriate method.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop, safe to use asyncio.run()
        return asyncio.run(coro)
    else:
        # Running loop exists, use run_until_complete
        return loop.run_until_complete(coro)

# Session ID prefix for Discord channels
DISCORD_SESSION_PREFIX = "discord:"

# Session ID prefix for Jira issues
JIRA_SESSION_PREFIX = "jira:"

# Compaction settings
COMPACT_INTERVAL_HOURS = 1  # How often to run compaction
MEMORY_ENTRIES_PER_FILE = 50  # Max entries per memory file


class SessionManager:
    """Session manager with persistence and compaction support."""

    def __init__(self, max_history: int = 5, auto_save: bool = True):
        self.max_history = max_history
        self.auto_save = auto_save
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._last_compact_time: Optional[datetime] = None
        
        # Auto-load sessions from disk on init
        self._load_all_sessions()

    def _load_all_sessions(self):
        """Load all sessions from disk."""
        try:
            sessions = _run_async(session_store.list_sessions())
            
            for session_info in sessions:
                session_key = self._get_session_key(session_info)
                if session_key:
                    self.sessions[session_key] = {
                        "history": [],
                        "created_at": session_info.get("createdAt", datetime.now().isoformat()),
                        "updated_at": session_info.get("updatedAt", datetime.now().isoformat()),
                        "_persisted": True,
                    }
            logger.info(f"Loaded {len(self.sessions)} persisted sessions")
        except Exception as e:
            logger.error(f"Failed to load sessions: {e}")

    def _get_session_key(self, session_info: Dict) -> Optional[str]:
        """Get session key from session info (reverse of create_session)."""
        session_id = session_info.get("sessionId", "")
        return session_id

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get or create a session."""
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "history": [],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "_persisted": False,
            }
            # Create persisted session
            if self.auto_save:
                try:
                    _run_async(
                        session_store.create_session(session_id, channel="default")
                    )
                except Exception as e:
                    logger.error(f"Failed to create persisted session: {e}")

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Add a message to the session history."""
        session = self.get_session(session_id)
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        session["history"].append(message)

        # Keep only recent history
        if len(session["history"]) > self.max_history * 2:
            session["history"] = session["history"][-self.max_history * 2 :]

        session["updated_at"] = datetime.now().isoformat()

        # Auto-save to persistence layer
        if self.auto_save and session.get("_persisted", False):
            try:
                _run_async(
                    session_store.append_message(session_id, role, content)
                )
            except Exception as e:
                logger.error(f"Failed to save message to persistence: {e}")

        # Check if we should run compaction
        self._check_compaction()

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """Get conversation history for a session."""
        session = self.get_session(session_id)
        return session["history"]

    def clear_history(self, session_id: str) -> None:
        """Clear session history."""
        if session_id in self.sessions:
            self.sessions[session_id]["history"] = []
            self.sessions[session_id]["updated_at"] = datetime.now().isoformat()
            # Clear persisted transcript
            if self.auto_save:
                try:
                    _run_async(
                        session_store.delete_session(session_id)
                    )
                except Exception as e:
                    logger.error(f"Failed to delete session from persistence: {e}")

    def list_sessions(self) -> List[str]:
        """List all active session IDs."""
        return list(self.sessions.keys())

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session metadata (without full history)."""
        if session_id not in self.sessions:
            return None
        session = self.sessions[session_id].copy()
        session["history_count"] = len(session.get("history", []))
        return session

    def _check_compaction(self):
        """Check if compaction should run."""
        now = datetime.now()
        if self._last_compact_time is None:
            self._last_compact_time = now
            return
        
        # Run compaction every COMPACT_INTERVAL_HOURS
        if (now - self._last_compact_time).total_seconds() > COMPACT_INTERVAL_HOURS * 3600:
            self._run_compaction()
            self._last_compact_time = now

    def _run_compaction(self):
        """Run compaction - extract important info and write to memory."""
        try:
            workspace_path = Path.home() / ".openclaw" / "workspace"
            memory_file = workspace_path / "MEMORY.md"
            
            # Collect important information from all sessions
            important_info = []
            
            for session_id, session in self.sessions.items():
                if not session.get("history"):
                    continue
                
                for msg in session["history"]:
                    if msg["role"] == "assistant":
                        content = msg["content"]
                        # Simple heuristic: save important looking content
                        if any(keyword in content.lower() for keyword in 
                               ["decided", "agreed", "important", "remember", "note"]):
                            important_info.append({
                                "session": session_id[:20],
                                "date": msg["timestamp"][:10],
                                "content": content[:200],
                            })
            
            if not important_info:
                return
            
            # Write to memory file
            memory_entries = []
            if memory_file.exists():
                with open(memory_file, 'r') as f:
                    content = f.read()
                existing = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('==')]
                memory_entries.extend(existing)
            
            for info in important_info:
                entry = f"- [{info['date']}] {info['session']}: {info['content']}"
                if entry not in memory_entries:
                    memory_entries.append(entry)
            
            # Keep only last 100 entries
            memory_entries = memory_entries[-100:]
            
            # Write back
            with open(memory_file, 'w') as f:
                f.write("# Long-term Memory\n\n")
                f.write(f"Last updated: {datetime.now().isoformat()}\n\n")
                f.write("## Important Decisions and Context\n\n")
                for entry in memory_entries:
                    f.write(entry + "\n")
            
            logger.info(f"Compaction: Added {len(important_info)} entries to memory")
            
        except Exception as e:
            logger.error(f"Compaction failed: {e}")

    def save_all(self):
        """Manually save all sessions to persistence layer."""
        try:
            for session_id, session in self.sessions.items():
                for msg in session.get("history", []):
                    _run_async(
                        session_store.append_message(session_id, msg["role"], msg["content"])
                    )
            logger.info(f"Saved {len(self.sessions)} sessions to persistence layer")
        except Exception as e:
            logger.error(f"Failed to save all sessions: {e}")


# Global session manager instance
session_manager = SessionManager()
