"""Session management for Engineering Flow Platform with persistence and TTL support."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import config
from src.sessions.persistence import session_persistence

logger = logging.getLogger(__name__)

# Session ID prefix for Jira issues
JIRA_SESSION_PREFIX = "jira:"

# Default settings
DEFAULT_MAX_HISTORY = 5
DEFAULT_TTL_SECONDS = 86400  # 24 hours
DEFAULT_AUTO_SAVE = True


class SessionManager:
    """Session manager with persistence, TTL, and automatic cleanup."""
    
    def __init__(
        self,
        max_history: int = None,
        auto_save: bool = None,
    ):
        self.max_history = max_history or config.session.get("max_history", DEFAULT_MAX_HISTORY)
        self.auto_save = auto_save if auto_save is not None else config.session.get("auto_save", DEFAULT_AUTO_SAVE)
        
        # Persistence settings
        persistence_config = config.session.get("persistence", {})
        self.persistence_enabled = persistence_config.get("enabled", True)
        self.persistence_ttl = persistence_config.get("ttl_seconds", DEFAULT_TTL_SECONDS)
        
        # Cache settings
        cache_config = config.session.get("cache", {})
        self.cache_enabled = cache_config.get("enabled", True)
        self.cache_max_sessions = cache_config.get("max_sessions", 1000)
        self.cache_ttl = cache_config.get("ttl_seconds", 3600)
        
        # In-memory session cache
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._session_timestamps: Dict[str, datetime] = {}
        self._initialized = False
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def initialize(self):
        """Load persisted sessions from disk and start cleanup task."""
        if self._initialized:
            return
        
        logger.info(f"Initializing session manager (persistence={self.persistence_enabled}, "
                   f"ttl={self.persistence_ttl}s)")
        
        if self.persistence_enabled:
            # Update persistence TTL
            session_persistence.ttl_seconds = self.persistence_ttl
            
            # Load persisted sessions
            persisted_sessions = await session_persistence.list_sessions(
                include_expired=False
            )
            
            for record in persisted_sessions:
                session_key = record.get("session_id", "")
                if session_key:
                    self.sessions[session_key] = {
                        "history": record.get("messages", []),
                        "channel": record.get("channel", ""),
                        "metadata": record.get("metadata", {}),
                        "created_at": record.get("created_at", datetime.now().isoformat()),
                        "updated_at": record.get("updated_at", datetime.now().isoformat()),
                        "_persisted": True,
                    }
                    self._session_timestamps[session_key] = datetime.now()
            
            logger.info(f"Loaded {len(self.sessions)} persisted sessions")
        
        self._initialized = True
        
        # Start background cleanup task
        if self.persistence_enabled:
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
    
    async def _periodic_cleanup(self):
        """Periodically cleanup expired sessions."""
        while True:
            try:
                await asyncio.sleep(3600)  # Run every hour
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup task error: {e}")
    
    async def _cleanup_expired(self):
        """Cleanup expired sessions from persistence and memory."""
        if not self.persistence_enabled:
            return
        
        try:
            # Remove expired from persistence
            removed = await session_persistence.cleanup_expired()
            
            # Remove expired from memory cache
            now = datetime.now()
            expired_keys = [
                key for key, timestamp in self._session_timestamps.items()
                if (now - timestamp).total_seconds() > self.cache_ttl
            ]
            
            for key in expired_keys:
                if key in self.sessions:
                    del self.sessions[key]
                del self._session_timestamps[key]
            
            if expired_keys:
                logger.info(f"Cleaned up {len(expired_keys)} expired sessions from cache")
            
        except Exception as e:
            logger.error(f"Failed to cleanup expired sessions: {e}")
    
    def _is_valid_session(self, session_id: str) -> bool:
        """Check if a session is still valid."""
        if not self.cache_enabled:
            return False
        
        if session_id not in self._session_timestamps:
            return False
        
        timestamp = self._session_timestamps[session_id]
        elapsed = (datetime.now() - timestamp).total_seconds()
        
        return elapsed <= self.cache_ttl
    
    def _update_timestamp(self, session_id: str):
        """Update session access timestamp."""
        if self.cache_enabled:
            self._session_timestamps[session_id] = datetime.now()
    
    def _evict_oldest_session(self):
        """Evict the oldest session from memory cache."""
        if not self.cache_enabled or len(self.sessions) <= self.cache_max_sessions:
            return
        
        # Find and remove the oldest session
        oldest_key = min(
            self._session_timestamps.keys(),
            key=lambda k: self._session_timestamps[k]
        )
        
        if oldest_key in self.sessions:
            del self.sessions[oldest_key]
        if oldest_key in self._session_timestamps:
            del self._session_timestamps[oldest_key]
        
        logger.debug(f"Evicted oldest session: {oldest_key}")
    
    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get or create a session."""
        # Check if session exists and is valid
        if session_id in self.sessions and self._is_valid_session(session_id):
            self._update_timestamp(session_id)
            return self.sessions[session_id]
        
        # Check persistence if enabled
        if self.persistence_enabled and session_id not in self.sessions:
            persisted = await session_persistence.load_session(session_id)
            
            if persisted:
                self.sessions[session_id] = {
                    "history": persisted.get("messages", []),
                    "user_id": persisted.get("user_id", ""),
                    "channel": persisted.get("channel", ""),
                    "metadata": persisted.get("metadata", {}),
                    "created_at": persisted.get("created_at", datetime.now().isoformat()),
                    "updated_at": persisted.get("updated_at", datetime.now().isoformat()),
                    "_persisted": True,
                }
                self._session_timestamps[session_id] = datetime.now()
                return self.sessions[session_id]
        
        # Create new session
        self._evict_oldest_session()
        
        self.sessions[session_id] = {
            "history": [],
            "user_id": "",
            "channel": "",
            "metadata": {},
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "_persisted": False,
        }
        self._session_timestamps[session_id] = datetime.now()
        
        return self.sessions[session_id]
    
    async def add_message(self, session_id: str, role: str, content: str, wait_for_save: bool = False) -> None:
        """Add a message to the session history.
        
        Args:
            session_id: The session ID
            role: Message role ('user', 'assistant', 'system')
            content: Message content
            wait_for_save: If True, wait for persistence save to complete
        """
        session = await self.get_session(session_id)
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        session["history"].append(message)
        session["updated_at"] = datetime.now().isoformat()
        
        # Keep only recent history
        if len(session["history"]) > self.max_history * 2:
            session["history"] = session["history"][-self.max_history * 2:]
        
        # Auto-save to persistence layer
        if self.auto_save and self.persistence_enabled:
            save_task = asyncio.create_task(
                session_persistence.save_session(
                    session_id=session_id,
                    channel=session.get("channel", ""),
                    messages=session["history"],
                    metadata=session.get("metadata", {}),
                )
            )
            # Optionally wait for save to complete
            if wait_for_save:
                await save_task
    
    async def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """Get conversation history for a session."""
        session = await self.get_session(session_id)
        return session.get("history", [])
    
    async def clear_history(self, session_id: str) -> None:
        """Clear session history."""
        if session_id in self.sessions:
            self.sessions[session_id]["history"] = []
            self.sessions[session_id]["updated_at"] = datetime.now().isoformat()
            
            if self.persistence_enabled:
                asyncio.create_task(
                    session_persistence.delete_session(session_id)
                )
    
    async def list_sessions(self) -> List[str]:
        """List all active session IDs."""
        return list(self.sessions.keys())
    
    async def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session metadata (without full history)."""
        if session_id not in self.sessions:
            return None
        
        session = self.sessions[session_id].copy()
        session["history_count"] = len(session.get("history", []))
        session["is_valid"] = self._is_valid_session(session_id)
        return session
    
    async def save_all(self):
        """Manually save all sessions to persistence layer."""
        if not self.persistence_enabled:
            return
        
        saved = 0
        for session_id, session in self.sessions.items():
            await session_persistence.save_session(
                session_id=session_id,
                channel=session.get("channel", ""),
                messages=session.get("history", []),
                metadata=session.get("metadata", {}),
            )
            saved += 1
        
        logger.info(f"Saved {saved} sessions to persistence")
    
    async def shutdown(self):
        """Shutdown session manager and cleanup."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Save all sessions before shutdown
        await self.save_all()
        
        logger.info("Session manager shutdown complete")


# Global session manager instance
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get or create the session manager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


# Default instance for backward compatibility
session_manager = SessionManager()
