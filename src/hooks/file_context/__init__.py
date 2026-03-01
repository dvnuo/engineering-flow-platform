"""File context module for AI integration with uploaded files.

This module provides:
- Session file metadata storage
- Chunk storage and retrieval
- Context injection for AI prompts
"""

from .models import (
    SessionFileMeta,
    SessionContext,
    Chunk,
    RetrievalRequest,
    RetrievalResult,
)

from .storage import FileContextStorage, storage

__all__ = [
    "SessionFileMeta",
    "SessionContext",
    "Chunk",
    "RetrievalRequest",
    "RetrievalResult",
    "FileContextStorage",
    "storage",
]
