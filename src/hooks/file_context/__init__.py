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
from .retrieval import RetrievalEngine, retrieval_engine
from .parser import CommandParser, FileReference, parser
from .injection import inject_context, build_rag_prompt, format_citations

__all__ = [
    # Models
    "SessionFileMeta",
    "SessionContext",
    "Chunk",
    "RetrievalRequest",
    "RetrievalResult",
    # Storage
    "FileContextStorage",
    "storage",
    # Retrieval
    "RetrievalEngine",
    "retrieval_engine",
    # Parser
    "CommandParser",
    "FileReference",
    "parser",
    # Injection
    "inject_context",
    "build_rag_prompt",
    "format_citations",
]
