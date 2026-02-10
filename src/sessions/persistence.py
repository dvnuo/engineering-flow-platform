"""Session persistence layer for Engineering Flow Platform.

Manages JSONL transcript files and sessions.json store with TTL support.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional


@dataclass
class SessionRecord:
    """Session record stored in JSONL format."""
    session_id: str
    user_id: str
    channel: str
    messages: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    created_at: str
    updated_at: str
    expires_at: Optional[str] = None
    
    def to_json(self) -> str:
        """Convert to JSON string for storage."""
        return json.dumps(asdict(self), ensure_ascii=False)
    
    @classmethod
    def from_json(cls, line: str) -> "SessionRecord":
        """Create from JSON string."""
        return cls(**json.loads(line))


class SessionPersistence:
    """Manages session persistence in JSONL format with TTL support.
    
    Directory structure:
        sessions/
        ├── sessions.json          # Store: sessionKey -> metadata
        ├── sessions.active.jsonl  # Active sessions (append-only)
        └── archive/
            └── sessions_YYYYMMDD_HHMMSS.jsonl  # Rotated files
    
    Features:
    - JSONL format for append-only logging
    - TTL (Time-To-Live) for automatic expiration
    - File rotation when size limit is reached
    - Background cleanup of expired sessions
    """
    
    def __init__(
        self,
        storage_dir: str = "~/.efp/sessions",
        ttl_seconds: int = 86400,  # 24 hours default
        max_file_size_mb: int = 100,
        enabled: bool = True,
    ):
        self.storage_dir = Path(storage_dir).expanduser()
        self.ttl_seconds = ttl_seconds
        self.max_file_size = max_file_size_mb * 1024 * 1024
        self.enabled = enabled
        self._lock = None  # Created lazily in ensure_dir
        self._ensure_dir()
    
    def _ensure_dir(self):
        """Ensure base directory exists."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.active_file = self.storage_dir / "sessions.active.jsonl"
        self.archive_dir = self.storage_dir / "archive"
        self.archive_dir.mkdir(exist_ok=True)
        if self._lock is None:
            self._lock = asyncio.Lock()
        # Create active file if it doesn't exist
        self.active_file.touch(exist_ok=True)
    
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
    
    def _rotate_file(self):
        """Rotate active file to archive when size limit is reached."""
        if not self.enabled:
            return
        if self.active_file.stat().st_size < self.max_file_size:
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_file = self.archive_dir / f"sessions_{timestamp}.jsonl"
        
        self.active_file.rename(archive_file)
        self.active_file.touch(exist_ok=True)
        
        logger = logging.getLogger(__name__)
        logger.info(f"Rotated session file to {archive_file}")
    
    async def save_session(
        self,
        session_id: str,
        channel: str,
        messages: List[Dict[str, Any]],
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Save or update a session to the active JSONL file."""
        if not self.enabled:
            return False
        
        async with self._lock:
            try:
                # Check file size and rotate if needed
                if self.active_file.stat().st_size > self.max_file_size:
                    self._rotate_file()
                
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
                
                with open(self.active_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
                
                return True
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to save session: {e}")
                return False
    
    async def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a session by ID from the active file."""
        if not self.enabled:
            return None
        
        try:
            with open(self.active_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("session_id") == session_id:
                        if self._is_expired(record):
                            return None
                        return record
            return None
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to load session: {e}")
            return None
    
    async def list_sessions(
        self,
        include_expired: bool = False,
    ) -> List[Dict[str, Any]]:
        """List all sessions, optionally including expired ones."""
        if not self.enabled:
            return []
        
        sessions = []
        now = datetime.utcnow()
        
        try:
            with open(self.active_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    
                    if not include_expired:
                        expires_at = record.get("expires_at")
                        if expires_at:
                            try:
                                expires = datetime.fromisoformat(expires_at)
                                if now > expires:
                                    continue
                            except (ValueError, TypeError):
                                continue
                    
                    sessions.append(record)
            
            return sessions
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to list sessions: {e}")
            return []
    
    async def delete_session(self, session_id: str) -> bool:
        """Delete a session by ID."""
        if not self.enabled:
            return False
        
        async with self._lock:
            try:
                sessions = []
                deleted = False
                
                with open(self.active_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        if record.get("session_id") != session_id:
                            sessions.append(line)
                        else:
                            deleted = True
                
                with open(self.active_file, 'w', encoding='utf-8') as f:
                    f.writelines(sessions)
                
                return deleted
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to delete session: {e}")
                return False
    
    async def cleanup_expired(self) -> int:
        """Remove all expired sessions from the active file."""
        if not self.enabled:
            return 0
        
        async with self._lock:
            try:
                sessions = []
                removed = 0
                now = datetime.utcnow()
                
                with open(self.active_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        
                        expires_at = record.get("expires_at")
                        is_expired = False
                        
                        if expires_at:
                            try:
                                expires = datetime.fromisoformat(expires_at)
                                is_expired = now > expires
                            except (ValueError, TypeError):
                                is_expired = True  # Remove malformed records
                        
                        if is_expired:
                            removed += 1
                        else:
                            sessions.append(line)
                
                with open(self.active_file, 'w', encoding='utf-8') as f:
                    f.writelines(sessions)
                
                logger = logging.getLogger(__name__)
                logger.info(f"Cleaned up {removed} expired sessions")
                return removed
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to cleanup expired sessions: {e}")
                return 0
    
    async def clear_all(self) -> int:
        """Clear all sessions. Returns count of cleared sessions."""
        if not self.enabled:
            return 0
        
        async with self._lock:
            try:
                count = 0
                with open(self.active_file, 'r', encoding='utf-8') as f:
                    count = sum(1 for line in f if line.strip())
                
                self.active_file.unlink()
                self.active_file.touch(exist_ok=True)
                
                logger = logging.getLogger(__name__)
                logger.info(f"Cleared {count} sessions")
                return count
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to clear sessions: {e}")
                return 0


# Global session store instance
session_persistence = SessionPersistence()
