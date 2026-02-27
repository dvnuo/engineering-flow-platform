"""Session persistence layer for Engineering Flow Platform.

Manages individual session files with TTL support.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionPersistence:
    """Manages session persistence with individual files per session.
    
    Directory structure:
        sessions/
        ├── {sanitized_session_id}_{hash}.jsonl    # Individual session files
        └── archive/                                # Archive for deleted sessions
    
    Filename format: {sanitized_session_id}_{12char_hash}.jsonl
    Session IDs are sanitized to safe characters and a hash suffix ensures uniqueness.
    
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
        """Get the file path for a session.
        
        Sanitizes session_id to prevent path traversal attacks.
        Uses hash to ensure uniqueness after sanitization.
        """
        # Sanitize session ID to prevent path traversal
        sanitized = "".join(c for c in session_id if c.isalnum() or c in "-_").strip()
        # Bound length to avoid filesystem limits (keep hash suffix)
        sanitized = sanitized[:100]
        
        # Add hash suffix to ensure uniqueness (e.g., "my-session" vs "my_session")
        short_hash = hashlib.md5(session_id.encode()).hexdigest()[:12]
        
        # Ensure filename doesn't start with special characters
        filename_base = f"{sanitized}_{short_hash}"
        if filename_base.startswith("-") or not sanitized:
            filename_base = f"session_{short_hash}"
        
        return self.storage_dir / f"{filename_base}.jsonl"
    
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
                
                # Preserve created_at for existing sessions
                created_at = now
                session_file = self._session_file(session_id)
                if session_file.exists():
                    try:
                        with open(session_file, 'r', encoding='utf-8') as f:
                            existing = json.loads(f.readline())
                            created_at = existing.get("created_at", now)
                    except Exception:
                        pass  # Use current time if unable to read
                
                record = {
                    "session_id": session_id,
                    "channel": channel,
                    "messages": messages,
                    "metadata": metadata or {},
                    "created_at": created_at,
                    "updated_at": now,
                    "expires_at": expires_at,
                }
                
                # Atomic write: write to temp file then rename
                temp_file = session_file.with_suffix('.tmp')
                try:
                    with open(temp_file, 'w', encoding='utf-8') as f:
                        f.write(json.dumps(record, ensure_ascii=False) + '\n')
                    temp_file.rename(session_file)
                except Exception:
                    # Clean up temp file on failure
                    if temp_file.exists():
                        temp_file.unlink()
                    raise
                
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
        
        try:
            for session_file in self.storage_dir.glob("*.jsonl"):
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
                
                # Get sanitized name for archive (use consistent logic with _session_file)
                sanitized = "".join(c for c in session_id if c.isalnum() or c in "-_").strip()
                short_hash = hashlib.md5(session_id.encode()).hexdigest()[:6]
                archive_base = f"{sanitized}_{short_hash}"
                if archive_base.startswith("-") or not sanitized:
                    archive_base = f"session_{short_hash}"
                
                # Move to archive with timestamp (include microseconds to avoid collision)
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
                archive_file = self.archive_dir / f"{archive_base}_{timestamp}.jsonl"
                
                # Handle filename collision (retry with new timestamp)
                retry = 0
                while archive_file.exists() and retry < 3:
                    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
                    archive_file = self.archive_dir / f"{archive_base}_{timestamp}_{retry}.jsonl"
                    retry += 1
                
                # If still exists after retries, use UUID to guarantee uniqueness
                if archive_file.exists():
                    archive_file = self.archive_dir / f"{archive_base}_{uuid.uuid4().hex}.jsonl"
                
                session_file.rename(archive_file)
                
                return True
            except Exception as e:
                logger.error(f"Failed to delete session {session_id}: {e}")
                return False
    
    async def cleanup_expired(self) -> int:
        """Archive all expired session files (for consistency with delete_session)."""
        if not self.enabled:
            return 0
        
        async with self._lock:
            try:
                # Materialize glob results to avoid unsafe iteration
                session_files = list(self.storage_dir.glob("*.jsonl"))
                archived = 0
                
                for session_file in session_files:
                    try:
                        # Check if file still exists (may have been deleted by another process)
                        if not session_file.exists():
                            continue
                        
                        with open(session_file, 'r', encoding='utf-8') as f:
                            line = f.readline()
                            if not line.strip():
                                continue
                            record = json.loads(line)
                            
                            if self._is_expired(record):
                                # Archive instead of delete (for consistency)
                                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
                                archive_file = self.archive_dir / f"{session_file.name.rsplit('.', 1)[0]}_{timestamp}.jsonl"
                                try:
                                    session_file.rename(archive_file)
                                    archived += 1
                                except FileNotFoundError:
                                    # File was removed between check and rename; safe to ignore
                                    logger.debug("Session file disappeared before archiving: %s", session_file)
                    except json.JSONDecodeError as e:
                        logger.warning("Failed to parse session file %s: %s", session_file, e)
                    except OSError as e:
                        logger.warning("Filesystem error processing session file %s: %s", session_file, e)
                    except Exception:
                        continue
                
                if archived > 0:
                    logger.info(f"Archived {archived} expired sessions")
                return archived
            except Exception as e:
                logger.error(f"Failed to cleanup expired sessions: {e}")
                return 0
    
    async def clear_all(self) -> int:
        """Clear all sessions by archiving them. Returns count of cleared sessions."""
        if not self.enabled:
            return 0
        
        async with self._lock:
            # Materialize glob results to avoid unsafe iteration
            session_files = list(self.storage_dir.glob("*.jsonl"))
            count = 0
            for session_file in session_files:
                # Archive instead of delete for consistency
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
                archive_file = self.archive_dir / f"{session_file.name.rsplit('.', 1)[0]}_{timestamp}.jsonl"
                try:
                    session_file.rename(archive_file)
                    count += 1
                except FileNotFoundError:
                    # File was removed between glob and rename; safe to ignore
                    logger.debug("Session file disappeared before clearing: %s", session_file)
                except OSError as e:
                    logger.warning("Filesystem error clearing session file %s: %s", session_file, e)
            
            logger.info(f"Cleared {count} sessions")
            return count


# Global session store instance
session_persistence = SessionPersistence()
