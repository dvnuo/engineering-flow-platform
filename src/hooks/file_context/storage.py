"""File context storage implementation."""

import json
import os
import hashlib
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from .models import SessionContext, SessionFileMeta, Chunk


class FileContextStorage:
    """File context storage handler."""
    
    def __init__(self, base_dir: str = "~/.efp/workspace/file_context"):
        self.base_dir = Path(base_dir).expanduser()
        self.sessions_dir = self.base_dir / "sessions"
        self.chunks_dir = self.base_dir / "chunks"
        
        # Ensure directories exist
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
    
    # ============ Session Methods ============
    
    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"
    
    def get_session_context(self, session_id: str) -> SessionContext:
        """Load session context."""
        path = self._session_path(session_id)
        if path.exists():
            data = json.loads(path.read_text())
            return SessionContext(**data)
        return SessionContext(session_id=session_id)
    
    def save_session_context(self, context: SessionContext) -> None:
        """Save session context."""
        path = self._session_path(context.session_id)
        path.write_text(context.model_dump_json(indent=2))
    
    def add_file_to_session(self, session_id: str, meta: SessionFileMeta) -> None:
        """Add file to session."""
        context = self.get_session_context(session_id)
        
        # Remove existing if any
        context.files = [f for f in context.files if f.file_id != meta.file_id]
        context.files.append(meta)
        context.updated_at = datetime.utcnow().isoformat() + "Z"
        
        self.save_session_context(context)
    
    def get_session_files(self, session_id: str) -> List[SessionFileMeta]:
        """Get all files in session."""
        context = self.get_session_context(session_id)
        return context.files
    
    def get_file_meta(self, session_id: str, file_id: str) -> Optional[SessionFileMeta]:
        """Get specific file metadata."""
        files = self.get_session_files(session_id)
        for f in files:
            if f.file_id == file_id:
                return f
        return None
    
    def update_file_status(
        self,
        session_id: str,
        file_id: str,
        status: str,
        error: Optional[str] = None,
        chunk_count: int = 0,
        total_chars: int = 0
    ) -> None:
        """Update file parse status."""
        context = self.get_session_context(session_id)
        
        for f in context.files:
            if f.file_id == file_id:
                f.parse_status = status
                if status == "completed":
                    f.parsed_at = datetime.utcnow().isoformat() + "Z"
                    f.chunk_count = chunk_count
                    f.total_chars = total_chars
                elif status == "failed":
                    f.parse_error = error
                
        context.updated_at = datetime.utcnow().isoformat() + "Z"
        self.save_session_context(context)
    
    # ============ Chunk Methods ============
    
    def _chunk_path(self, file_id: str, chunk_id: str) -> Path:
        file_dir = self.chunks_dir / file_id
        file_dir.mkdir(parents=True, exist_ok=True)
        return file_dir / f"{chunk_id}.json"
    
    def save_chunk(self, chunk: Chunk) -> None:
        """Save chunk to storage."""
        path = self._chunk_path(chunk.file_id, chunk.chunk_id)
        path.write_text(chunk.model_dump_json(indent=2))
    
    def save_chunks(self, chunks: List[Chunk]) -> None:
        """Save multiple chunks."""
        for chunk in chunks:
            self.save_chunk(chunk)
    
    def get_chunk(self, file_id: str, chunk_id: str) -> Optional[Chunk]:
        """Load chunk from storage."""
        path = self._chunk_path(file_id, chunk_id)
        if path.exists():
            data = json.loads(path.read_text())
            return Chunk(**data)
        return None
    
    def get_file_chunks(self, file_id: str) -> List[Chunk]:
        """Get all chunks for a file."""
        file_dir = self.chunks_dir / file_id
        if not file_dir.exists():
            return []
        
        chunks = []
        for path in file_dir.glob("*.json"):
            data = json.loads(path.read_text())
            chunks.append(Chunk(**data))
        
        # Sort by page and index
        chunks.sort(key=lambda c: (c.page or 0, c.index))
        return chunks
    
    def get_session_chunks(self, session_id: str) -> List[Chunk]:
        """Get all chunks for all files in session."""
        files = self.get_session_files(session_id)
        all_chunks = []
        for f in files:
            if f.parse_status == "completed":
                all_chunks.extend(self.get_file_chunks(f.file_id))
        return all_chunks
    
    def get_session_completed_files(self, session_id: str) -> List[SessionFileMeta]:
        """Get files with completed parse status."""
        files = self.get_session_files(session_id)
        return [f for f in files if f.parse_status == "completed"]
    
    # ============ Utility Methods ============
    
    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Compute SHA256 hash for deduplication."""
        return hashlib.sha256(content.encode()).hexdigest()
    
    def delete_file_chunks(self, file_id: str) -> int:
        """Delete all chunks for a file. Returns count deleted."""
        file_dir = self.chunks_dir / file_id
        if not file_dir.exists():
            return 0
        
        count = 0
        for path in file_dir.glob("*.json"):
            path.unlink()
            count += 1
        
        # Remove directory if empty
        try:
            file_dir.rmdir()
        except OSError:
            pass
        
        return count
    
    def delete_session(self, session_id: str) -> int:
        """Delete all files and chunks for a session. Returns count deleted."""
        files = self.get_session_files(session_id)
        
        # Delete chunks for each file
        for f in files:
            self.delete_file_chunks(f.file_id)
        
        # Delete session context
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
        
        return len(files)


# Global instance
storage = FileContextStorage()
