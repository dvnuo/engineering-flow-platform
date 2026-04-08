"""Session management for Engineering Flow Platform with persistence and TTL support."""

import asyncio
from copy import deepcopy
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import config
from src.sessions.persistence import session_persistence

logger = logging.getLogger(__name__)

# Session ID prefix for Jira issues
JIRA_SESSION_PREFIX = "jira:"

# Default settings
DEFAULT_MAX_HISTORY = 999999  # Effectively unlimited
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
        """Evict the oldest session from memory cache.
        
        Before eviction, the session is automatically summarized and saved
        to daily memory files for future context.
        """
        if not self.cache_enabled or len(self.sessions) <= self.cache_max_sessions:
            return
        
        # Find the oldest session
        oldest_key = min(
            self._session_timestamps.keys(),
            key=lambda k: self._session_timestamps[k]
        )
        
        # Get session data before deletion (to avoid race condition)
        session_data = self.sessions.get(oldest_key)
        
        # Auto-save session summary with pre-fetched data
        if session_data and session_data.get("history"):
            try:
                from src.hooks.session_memory import save_session_summary
                # Pass session data directly to avoid race condition
                asyncio.create_task(save_session_summary(
                    oldest_key, 
                    workspace_dir=None,
                    session_data=session_data
                ))
                logger.debug(f"Scheduled auto-save for session {oldest_key}")
            except Exception as e:
                logger.warning(f"Failed to auto-save session {oldest_key}: {e}")
        
        # Remove the session
        if oldest_key in self.sessions:
            del self.sessions[oldest_key]
        if oldest_key in self._session_timestamps:
            del self._session_timestamps[oldest_key]
        
        logger.debug(f"Evicted oldest session: {oldest_key}")

    @staticmethod
    def _restore_active_skill_session_from_metadata(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Restore legacy active skill session from metadata if needed.

        Kept for backward compatibility with persisted historical sessions.
        Runtime skill execution no longer depends on this field.
        """
        active = session.get("active_skill_session")
        if active is not None:
            return active

        metadata = session.get("metadata", {})
        active = metadata.get("active_skill_session")
        if active is not None:
            session["active_skill_session"] = active
        return active
    
    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get or create a session."""
        # Check if session exists and is valid
        if session_id in self.sessions and self._is_valid_session(session_id):
            self._restore_active_skill_session_from_metadata(self.sessions[session_id])
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
                self._restore_active_skill_session_from_metadata(self.sessions[session_id])
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
    
    async def add_message(self, session_id: str, role: str, content: str, wait_for_save: bool = False, extra: dict = None) -> str:
        """Add a message to the session history.
        
        Args:
            session_id: The session ID
            role: Message role ('user', 'assistant', 'system', 'tool')
            content: Message content
            wait_for_save: If True, wait for persistence save to complete
            extra: Optional extra fields like tool_call_id for tool messages
            
        Returns:
            The unique message ID that was created
        """
        session = await self.get_session(session_id)
        message_id = str(uuid.uuid4())
        message = {
            "id": message_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        # Add extra fields (e.g., tool_call_id for tool messages)
        if extra:
            message.update(extra)
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
        
        return message_id  # Return the message ID for reference
    
    async def edit_message(self, session_id: str, message_id: str, new_content: str) -> bool:
        """Edit a message in the session history.
        
        Args:
            session_id: The session ID
            message_id: The unique message ID to edit
            new_content: The new content for the message
            
        Returns:
            True if message was found and edited, False otherwise
        """
        session = await self.get_session(session_id)
        for msg in session["history"]:
            if msg.get("id") == message_id:
                msg["content"] = new_content
                msg["timestamp"] = datetime.now().isoformat()
                session["updated_at"] = datetime.now().isoformat()
                # Auto-save after editing
                if self.auto_save and self.persistence_enabled:
                    asyncio.create_task(
                        session_persistence.save_session(
                            session_id=session_id,
                            channel=session.get("channel", ""),
                            messages=session["history"],
                            metadata=session.get("metadata", {}),
                        )
                    )
                return True
        return False
    
    async def delete_message(self, session_id: str, message_id: str) -> bool:
        """Delete a specific message by ID.
        
        Args:
            session_id: The session ID
            message_id: The message ID to delete
            
        Returns:
            True if deleted, False if not found
        """
        session = await self.get_session(session_id)
        
        history = session.get("history", [])
        
        # Find and remove the message
        for i, msg in enumerate(history):
            if msg.get("id") == message_id:
                history.pop(i)
                session["updated_at"] = datetime.now().isoformat()
                
                # Auto-save after deletion
                if self.auto_save and self.persistence_enabled:
                    asyncio.create_task(
                        session_persistence.save_session(
                            session_id=session_id,
                            channel=session.get("channel", ""),
                            messages=history,
                            metadata=session.get("metadata", {}),
                        )
                    )
                return True
        
        return False
    
    async def delete_messages_after(self, session_id: str, message_id: str) -> int:
        """Delete all messages after the specified message (exclusive).
        
        Args:
            session_id: The session ID
            message_id: The message ID - all messages after this will be deleted
            
        Returns:
            Number of messages deleted
        """
        session = await self.get_session(session_id)
        history = session.get("history", [])
        
        # Find the index of the message
        target_index = -1
        for i, msg in enumerate(history):
            if msg.get("id") == message_id:
                target_index = i
                break
        
        if target_index == -1:
            return 0  # Message not found
        
        # Delete all messages AFTER the target (keep the target message itself)
        deleted_count = len(history) - target_index - 1
        session["history"] = history[:target_index + 1]  # Keep up to and including target
        session["updated_at"] = datetime.now().isoformat()
        
        # Auto-save after deletion
        if self.auto_save and self.persistence_enabled:
            asyncio.create_task(
                session_persistence.save_session(
                    session_id=session_id,
                    channel=session.get("channel", ""),
                    messages=session["history"],
                    metadata=session.get("metadata", {}),
                )
            )
        
        return deleted_count
    
    async def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """Get conversation history for a session."""
        session = await self.get_session(session_id)
        return session.get("history", [])
    

    async def get_active_skill_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get legacy active skill session state for a chat session."""
        session = await self.get_session(session_id)
        active = session.get("active_skill_session")
        if active:
            return active

        metadata = session.get("metadata", {})
        active = metadata.get("active_skill_session")
        if active:
            session["active_skill_session"] = active
        return active

    async def set_active_skill_session(self, session_id: str, skill_session: Optional[Dict[str, Any]]) -> None:
        """Set or clear legacy active skill session state for a chat session."""
        session = await self.get_session(session_id)
        session["active_skill_session"] = skill_session
        metadata = session.setdefault("metadata", {})
        metadata["active_skill_session"] = skill_session
        session["updated_at"] = datetime.now().isoformat()

        if self.auto_save and self.persistence_enabled:
            asyncio.create_task(
                session_persistence.save_session(
                    session_id=session_id,
                    channel=session.get("channel", ""),
                    messages=session.get("history", []),
                    metadata=session.get("metadata", {}),
                )
            )

    async def set_last_execution_id(self, session_id: str, request_id: Optional[str]) -> None:
        """Record latest runtime execution request id in session metadata.

        This updates in-memory session metadata and schedules asynchronous
        metadata persistence without blocking the current request path. This
        pattern is used for lightweight metadata-only state transitions.
        """
        if not session_id or not request_id:
            return
        session = await self.get_session(session_id)
        metadata = session.setdefault("metadata", {})
        metadata["last_execution_id"] = request_id
        session["updated_at"] = datetime.now().isoformat()
        self._schedule_metadata_persist(session_id, session)

    async def add_pending_delegation(self, session_id: str, delegation_record: Dict[str, Any]) -> None:
        """Add a lightweight pending delegation record to session metadata."""
        if not session_id or not isinstance(delegation_record, dict):
            return
        session = await self.get_session(session_id)
        metadata = session.setdefault("metadata", {})
        pending = metadata.get("pending_delegations")
        pending_list = list(pending) if isinstance(pending, list) else []
        pending_dicts = [item for item in pending_list if isinstance(item, dict)]
        delegation_id = delegation_record.get("delegation_id")
        if delegation_id:
            pending_dicts = [item for item in pending_dicts if item.get("delegation_id") != delegation_id]
        pending_dicts.append(dict(delegation_record))
        metadata["pending_delegations"] = pending_dicts
        session["updated_at"] = datetime.now().isoformat()
        self._schedule_metadata_persist(session_id, session)

    async def complete_pending_delegation(
        self,
        session_id: str,
        delegation_id: str,
        *,
        status: str,
    ) -> None:
        """Mark pending delegation as completed and remove from pending list."""
        if not session_id or not delegation_id:
            return
        session = await self.get_session(session_id)
        metadata = session.setdefault("metadata", {})
        pending = metadata.get("pending_delegations")
        pending_list = list(pending) if isinstance(pending, list) else []
        matched: Optional[Dict[str, Any]] = None
        remaining: List[Dict[str, Any]] = []
        for item in pending_list:
            if not isinstance(item, dict):
                continue
            if item.get("delegation_id") == delegation_id:
                matched = dict(item)
                continue
            remaining.append(item)
        metadata["pending_delegations"] = remaining
        if matched is not None:
            completed = metadata.get("completed_delegations")
            completed_list = list(completed) if isinstance(completed, list) else []
            matched["status"] = status
            matched["completed_at"] = datetime.now().isoformat()
            completed_list.append(matched)
            metadata["completed_delegations"] = completed_list[-50:]
        session["updated_at"] = datetime.now().isoformat()
        self._schedule_metadata_persist(session_id, session)

    def _schedule_metadata_persist(self, session_id: str, session: Dict[str, Any]) -> None:
        """Persist metadata-only state transitions without blocking request flow."""
        if not self.auto_save or not self.persistence_enabled:
            return
        channel_snapshot = str(session.get("channel", ""))
        messages_snapshot = deepcopy(session.get("history", []))
        metadata_snapshot = deepcopy(session.get("metadata", {}))

        async def _persist_metadata_snapshot() -> None:
            try:
                await session_persistence.save_session(
                    session_id=session_id,
                    channel=channel_snapshot,
                    messages=messages_snapshot,
                    metadata=metadata_snapshot,
                )
            except Exception:
                logger.exception("Failed to persist metadata-only session update", extra={"session_id": session_id})

        asyncio.create_task(_persist_metadata_snapshot())

    async def recover_session_state(self, session_id: str) -> Dict[str, Any]:
        """Recover runtime-facing session state through the runtime recovery pipeline."""
        from src.runtime.recovery_pipeline import get_recovery_pipeline

        pipeline = get_recovery_pipeline()
        hydration = await pipeline.hydrate_session_state(session_id)
        return {
            "session_id": hydration.session_id,
            "recovered": hydration.recovered,
            "snapshot_version": hydration.snapshot_version,
            "active_skill_session": hydration.active_skill_session,
            "last_execution_id": hydration.last_execution_id,
            "runtime_state": dict(hydration.runtime_state),
            "reconstructed_state": dict(hydration.reconstructed_state),
            "warnings": list(hydration.warnings),
            "runtime_events": list(hydration.runtime_events),
            "metadata": dict(hydration.metadata),
        }

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
