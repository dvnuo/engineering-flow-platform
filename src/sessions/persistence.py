"""Session persistence layer for Engineering Flow Platform.

Manages individual session files with TTL support.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionPersistence:
    """Manages session persistence with individual files per session.
    
    Directory structure:
        sessions/
        ├── {session_id}.jsonl    # Individual session files
        └── archive/              # Archive for deleted sessions
    
    Features:
    - One file per session
    - TTL (Time-To-Live) for automatic expiration
    - Background cleanup of expired sessions
    """
    
    def __init__(
        self,
        storage_dir: str = "~/.efp/workspace/sessions",
        ttl_seconds: int = 2592000,  # 30 days default
        enabled: bool = True,
    ):
        self.storage_dir = Path(storage_dir).expanduser()
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        self._lock = asyncio.Lock()
        self._ensure_dir()
    
    def _ensure_dir(self):
        """Ensure base directory exists."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir = self.storage_dir / "archive"
        self.archive_dir.mkdir(exist_ok=True)
    
    def _session_file(self, session_id: str) -> Path:
        """Get the file path for a session."""
        return self.storage_dir / f"{session_id}.jsonl"
    
    def _is_expired(self, record: Dict) -> bool:
        """Check if a session record is expired."""
        expires_at = record.get("expires_at")
        if not expires_at:
            return False
        try:
            expires = datetime.fromisoformat(expires_at)
            return datetime.utcnow() > expires
        except (ValueError, TypeError):
            return False
    
    def _calculate_expires_at(self) -> str:
        """Calculate expiration time for new sessions."""
        expires = datetime.utcnow() + timedelta(seconds=self.ttl_seconds)
        return expires.isoformat()
    
    async def save_session(
        self,
        session_id: str,
        channel: str,
        messages: List[Dict[str, Any]],
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Save or update a session to its individual file."""
        if not self.enabled:
            return False
        
        async with self._lock:
            try:
                now = datetime.utcnow().isoformat()
                expires_at = self._calculate_expires_at()
                
                record = {
                    "session_id": session_id,
                    "channel": channel,
                    "messages": messages,
                    "metadata": metadata or {},
                    "created_at": now,
                    "updated_at": now,
                    "expires_at": expires_at,
                }
                
                session_file = self._session_file(session_id)
                with open(session_file, 'w', encoding='utf-8') as f:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
                
                return True
            except Exception as e:
                logger.error(f"Failed to save session {session_id}: {e}")
                return False
    
    async def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a session by ID from its file."""
        if not self.enabled:
            return None
        
        try:
            session_file = self._session_file(session_id)
            if not session_file.exists():
                return None
            
            with open(session_file, 'r', encoding='utf-8') as f:
                line = f.readline()
                if not line.strip():
                    return None
                record = json.loads(line)
                
                if self._is_expired(record):
                    return None
                return record
        except Exception as e:
            logger.error(f"Failed to load session {session_id}: {e}")
            return None
    
    async def list_sessions(
        self,
        include_expired: bool = False,
    ) -> List[Dict[str, Any]]:
        """List all sessions from individual files."""
        if not self.enabled:
            return []
        
        sessions = []
        now = datetime.utcnow()
        
        try:
            for session_file in self.storage_dir.glob("*.jsonl"):
                # Skip archive directory
                if session_file.parent != self.storage_dir:
                    continue
                
                try:
                    with open(session_file, 'r', encoding='utf-8') as f:
                        line = f.readline()
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        
                        if not include_expired and self._is_expired(record):
                            continue
                        
                        sessions.append(record)
                except Exception:
                    continue
            
            return sessions
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return []
    
    async def delete_session(self, session_id: str) -> bool:
        """Delete a session by moving its file to archive."""
        if not self.enabled:
            return False
        
        async with self._lock:
            try:
                session_file = self._session_file(session_id)
                if not session_file.exists():
                    return False
                
                # Move to archive with timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                archive_file = self.archive_dir / f"{session_id}_{timestamp}.jsonl"
                session_file.rename(archive_file)
                
                return True
            except Exception as e:
                logger.error(f"Failed to delete session {session_id}: {e}")
                return False
    
    async def cleanup_expired(self) -> int:
        """Remove all expired session files."""
        if not self.enabled:
            return 0
        
        async with self._lock:
            try:
                removed = 0
                now = datetime.utcnow()
                
                for session_file in self.storage_dir.glob("*.jsonl"):
                    if session_file.parent != self.storage_dir:
                        continue
                    
                    try:
                        with open(session_file, 'r', encoding='utf-8') as f:
                            line = f.readline()
                            if not line.strip():
                                continue
                            record = json.loads(line)
                            
                            if self._is_expired(record):
                                session_file.unlink()
                                removed += 1
                    except Exception:
                        continue
                
                if removed > 0:
                    logger.info(f"Cleaned up {removed} expired sessions")
                return removed
            except Exception as e:
                logger.error(f"Failed to cleanup expired sessions: {e}")
                return 0
    
    async def clear_all(self) -> int:
        """Clear all sessions. Returns count of cleared sessions."""
        if not self.enabled:
            return 0
        
        async with self._lock:
            try:
                count = 0
                for session_file in self.storage_dir.glob("*.jsonl"):
                    if session_file.parent != self.storage_dir:
                        continue
                    session_file.unlink()
                    count += 1
                
                logger.info(f"Cleared {count} sessions")
                return count
            except Exception as e:
                logger.error(f"Failed to clear sessions: {e}")
                return 0


# Global session store instance
session_persistence = SessionPersistence()
