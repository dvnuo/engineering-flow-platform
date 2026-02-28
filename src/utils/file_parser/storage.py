"""File storage with metadata mapping."""

import os
from pathlib import Path
from typing import Dict, Optional
import uuid
from datetime import datetime

from .models import FileMetadata, FileNotFoundError
from .validators import get_safe_extension


# Upload directory
UPLOAD_DIR = Path("~/.efp/workspace/uploads").expanduser()

# In-memory metadata storage (use Redis in production)
_file_metadata: Dict[str, FileMetadata] = {}


def init_storage() -> None:
    """Initialize storage directory."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _generate_file_id() -> str:
    """Generate unique file ID."""
    return uuid.uuid4().hex


def register_file(
    file_id: str,
    original_filename: str,
    stored_filename: str,
    content_type: str,
    size: int,
    session_id: Optional[str] = None
) -> FileMetadata:
    """Register file metadata.
    
    Args:
        file_id: UUID
        original_filename: User's original filename
        stored_filename: Server-side filename
        content_type: MIME type
        size: File size in bytes
        session_id: Optional session ID
        
    Returns:
        Created FileMetadata
    """
    metadata = FileMetadata(
        file_id=file_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        content_type=content_type,
        size=size,
        uploaded_at=datetime.now().isoformat(),
        session_id=session_id,
    )
    _file_metadata[file_id] = metadata
    return metadata


def get_file_path(file_id: str) -> Path:
    """Get file path by ID from metadata.
    
    ⚠️ Don't use glob - use metadata mapping for security.
    
    Args:
        file_id: UUID
        
    Returns:
        Path to the file
        
    Raises:
        FileNotFoundError: If file not found
    """
    if file_id not in _file_metadata:
        raise FileNotFoundError(f"File not found: {file_id}")
    
    stored_name = _file_metadata[file_id].stored_filename
    path = UPLOAD_DIR / stored_name
    
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_id}")
    
    return path


def get_metadata(file_id: str) -> FileMetadata:
    """Get file metadata.
    
    Args:
        file_id: UUID
        
    Returns:
        FileMetadata
        
    Raises:
        FileNotFoundError: If not found
    """
    if file_id not in _file_metadata:
        raise FileNotFoundError(f"File not found: {file_id}")
    return _file_metadata[file_id]


def list_files(session_id: Optional[str] = None) -> list:
    """List files, optionally filtered by session.
    
    Args:
        session_id: Optional session ID to filter
        
    Returns:
        List of FileMetadata
    """
    if session_id:
        return [m for m in _file_metadata.values() if m.session_id == session_id]
    return list(_file_metadata.values())


def delete_file(file_id: str) -> bool:
    """Delete file and metadata.
    
    Args:
        file_id: UUID
        
    Returns:
        True if deleted
    """
    if file_id not in _file_metadata:
        return False
    
    # Get stored filename and delete
    stored_name = _file_metadata[file_id].stored_filename
    path = UPLOAD_DIR / stored_name
    
    if path.exists():
        path.unlink()
    
    # Remove metadata
    del _file_metadata[file_id]
    return True


async def save_uploaded_file(
    content: bytes,
    original_filename: str,
    session_id: Optional[str] = None
) -> FileMetadata:
    """Save uploaded file to storage.
    
    Storage name: {file_id}{ext} (server-side canonical)
    Original filename: stored only in metadata
    
    Args:
        content: File content bytes
        original_filename: User's original filename
        session_id: Optional session ID
        
    Returns:
        Created FileMetadata
    """
    init_storage()
    
    # Generate unique ID
    file_id = _generate_file_id()
    
    # Sanitize original filename to prevent XSS
    from .validators import sanitize_filename
    original_filename = sanitize_filename(original_filename)
    
    # Get safe extension from original filename first
    ext = get_safe_extension(original_filename)
    
    # Detect MIME type based on extension
    content_type = _detect_mime_type(content, ext)
    
    # Get safe extension from MIME type (not user input)
    ext = _mime_to_extension(content_type)
    
    # Server-side canonical name
    stored_filename = f"{file_id}{ext}"
    file_path = UPLOAD_DIR / stored_filename
    
    # Write file (async to avoid blocking event loop)
    import asyncio
    await asyncio.to_thread(file_path.write_bytes, content)
    
    # Get MIME type
    content_type = _detect_mime_type(content, ext)
    
    # Register metadata
    return register_file(
        file_id=file_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        content_type=content_type,
        size=len(content),
        session_id=session_id
    )


def _detect_mime_type(content: bytes, ext: str) -> str:
    """Detect MIME type from content or extension.
    
    Args:
        content: File content
        ext: File extension
        
    Returns:
        MIME type
    """
    try:
        import magic
        return magic.from_buffer(content[:1024], mime=True)
    except Exception:
        # Fallback to extension
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".csv": "text/csv",
            ".txt": "text/plain",
        }
        return mime_map.get(ext.lower(), "application/octet-stream")


def _mime_to_extension(mime_type: str) -> str:
    """Get safe extension from MIME type.
    
    Args:
        mime_type: Detected MIME type
        
    Returns:
        Safe extension with dot
    """
    mime_to_ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "text/csv": ".csv",
        "text/plain": ".txt",
    }
    return mime_to_ext.get(mime_type, "")
