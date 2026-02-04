"""Session persistence layer for OpenClaw Mini.

Manages JSONL transcript files and sessions.json store.
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional


class SessionStore:
    """Manages session persistence in JSONL format.
    
    Directory structure:
        sessions/
        ├── sessions.json          # Store: sessionKey -> metadata
        ├── main/
        │   └── <sessionId>.jsonl  # Transcript files
        └── <channel>/
            └── <sessionId>.jsonl
    """
    
    def __init__(self, base_path: str = "~/.opsclaw/opsclaw/sessions"):
        self.base_path = Path(base_path).expanduser()
        self.sessions_file = self.base_path / "sessions.json"
        self._lock = None  # Created lazily in ensure_dir
    
    async def ensure_dir(self):
        """Ensure base directory exists."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        if self._lock is None:
            self._lock = asyncio.Lock()
        
    def _load_store(self) -> Dict[str, Any]:
        """Load sessions.json store."""
        if self.sessions_file.exists():
            try:
                with open(self.sessions_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}
    
    def _save_store(self, store: Dict[str, Any]):
        """Save sessions.json store."""
        with open(self.sessions_file, 'w') as f:
            json.dump(store, f, indent=2)
    
    def _get_transcript_path(self, channel: str, session_id: str) -> Path:
        """Get path for transcript file."""
        channel_dir = self.base_path / channel
        channel_dir.mkdir(parents=True, exist_ok=True)
        return channel_dir / f"{session_id}.jsonl"
    
    async def create_session(
        self,
        session_key: str,
        channel: str = "main",
        metadata: Optional[Dict] = None
    ) -> str:
        """Create a new session and return session_id."""
        async with self._lock:
            store = self._load_store()
            
            # Generate session ID
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"{channel}_{timestamp}"
            
            # Store metadata
            store[session_key] = {
                "sessionId": session_id,
                "channel": channel,
                "createdAt": datetime.now().isoformat(),
                "updatedAt": datetime.now().isoformat(),
                "messageCount": 0,
                "metadata": metadata or {},
            }
            
            self._save_store(store)
            
            # Initialize transcript file
            transcript_path = self._get_transcript_path(channel, session_id)
            transcript_path.touch()
            
            return session_id
    
    async def get_session_id(self, session_key: str) -> Optional[str]:
        """Get session_id for a session key."""
        store = self._load_store()
        session = store.get(session_key)
        return session["sessionId"] if session else None
    
    async def get_session_info(self, session_key: str) -> Optional[Dict[str, Any]]:
        """Get session metadata."""
        store = self._load_store()
        return store.get(session_key)
    
    async def update_session(
        self,
        session_key: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Update session metadata."""
        async with self._lock:
            store = self._load_store()
            if session_key not in store:
                return False
            
            store[session_key].update(updates)
            store[session_key]["updatedAt"] = datetime.now().isoformat()
            self._save_store(store)
            return True
    
    async def append_message(
        self,
        session_key: str,
        role: str,
        content: str,
        metadata: Optional[Dict] = None
    ) -> bool:
        """Append a message to the transcript."""
        store = self._load_store()
        if session_key not in store:
            return False
        
        session_info = store[session_key]
        channel = session_info["channel"]
        session_id = session_info["sessionId"]
        
        transcript_path = self._get_transcript_path(channel, session_id)
        
        entry = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        
        with open(transcript_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
        
        # Update message count
        session_info["messageCount"] = session_info.get("messageCount", 0) + 1
        session_info["updatedAt"] = datetime.now().isoformat()
        self._save_store(store)
        
        return True
    
    async def get_transcript(
        self,
        session_key: str,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get transcript history for a session."""
        store = self._load_store()
        if session_key not in store:
            return []
        
        session_info = store[session_key]
        channel = session_info["channel"]
        session_id = session_info["sessionId"]
        
        transcript_path = self._get_transcript_path(channel, session_id)
        if not transcript_path.exists():
            return []
        
        messages = []
        with open(transcript_path, 'r') as f:
            for line in f:
                if line.strip():
                    messages.append(json.loads(line))
        
        if limit:
            messages = messages[-limit:]
        
        return messages
    
    async def list_sessions(
        self,
        active_minutes: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List all sessions, optionally filtered by activity."""
        store = self._load_store()
        sessions = list(store.values())
        
        if active_minutes:
            cutoff = datetime.now().timestamp() - (active_minutes * 60)
            sessions = [
                s for s in sessions
                if datetime.fromisoformat(s["updatedAt"]).timestamp() > cutoff
            ]
        
        return sessions
    
    async def delete_session(self, session_key: str) -> bool:
        """Delete a session and its transcript."""
        async with self._lock:
            store = self._load_store()
            if session_key not in store:
                return False
            
            session_info = store[session_key]
            channel = session_info["channel"]
            session_id = session_info["sessionId"]
            
            # Delete transcript file
            transcript_path = self._get_transcript_path(channel, session_id)
            if transcript_path.exists():
                transcript_path.unlink()
            
            # Remove from store
            del store[session_key]
            self._save_store(store)
            
            return True
    
    async def clear_all(self) -> int:
        """Clear all sessions. Returns count deleted."""
        async with self._lock:
            store = self._load_store()
            count = len(store)
            
            # Delete all transcript files
            for session_info in store.values():
                channel = session_info["channel"]
                session_id = session_info["sessionId"]
                transcript_path = self._get_transcript_path(channel, session_id)
                if transcript_path.exists():
                    transcript_path.unlink()
            
            # Clear store
            self._save_store({})
            
            return count


# Global session store instance
session_store = SessionStore()
