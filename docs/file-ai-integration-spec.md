# File AI Integration - Technical Specification

> This document provides detailed implementation guidance for the File AI Integration feature.
> Based on requirements: `docs/file-ai-integration.md`

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Data Models](#2-data-models)
3. [Storage Layer](#3-storage-layer)
4. [Retrieval System](#4-retrieval-system)
5. [AI Context Injection](#5-ai-context-injection)
6. [Command Parser](#6-command-parser)
7. [API Reference](#7-api-reference)
8. [Database Schema](#8-database-schema)
9. [Testing Strategy](#9-testing-strategy)
10. [Implementation Checklist](#10-implementation-checklist)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Interface                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  File List  │  │   Chat UI   │  │  Chunk Preview Panel   │ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
└─────────┼─────────────────┼───────────────────────┼───────────────┘
          │                 │                       │
          ▼                 ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Gateway (webchat.py)                         │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────┐│
│  │ Upload API   │  │ Context API   │  │ Retrieval API         ││
│  └──────┬───────┘  └───────┬───────┘  └──────────┬───────────┘│
└─────────┼─────────────────┼──────────────────────┼────────────┘
          │                 │                      │
          ▼                 ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                     File Context Layer                           │
│  ┌────────────────┐  ┌────────────────┐  ┌─────────────────┐  │
│  │ FileContext     │  │ ChunkStore     │  │ RetrievalEngine  │  │
│  │ (Session Meta) │  │ (Chunk Storage)│  │ (Search + Rank) │  │
│  └────────────────┘  └────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
          │                 │                      │
          ▼                 ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Storage (File-based)                          │
│  ~/.efp/workspace/file_context/sessions/  (session metadata)   │
│  ~/.efp/workspace/file_context/chunks/     (chunk data)        │
│  ~/.efp/workspace/file_context/index/      (search index)      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Data Models

### 2.1 Session File Metadata

**File:** `src/hooks/file_context/models.py`

```python
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class SessionFileMeta(BaseModel):
    """Lightweight session file metadata."""
    
    file_id: str = Field(..., description="Unique file identifier")
    session_id: str = Field(..., description="Session identifier")
    filename: str = Field(..., description="Original filename")
    content_type: str = Field(..., description="MIME type")
    parse_status: str = Field(
        default="pending",
        description="pending|processing|completed|failed"
    )
    parse_error: Optional[str] = Field(None, description="Error message if failed")
    parsed_at: Optional[str] = Field(None, description="ISO timestamp")
    chunk_count: int = Field(default=0, description="Number of chunks")
    total_chars: int = Field(default=0, description="Total character count")
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    model_config = {"populate_by_name": True}


class SessionContext(BaseModel):
    """Session file context container."""
    session_id: str
    files: List[SessionFileMeta] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
```

### 2.2 Chunk Data

```python
class Chunk(BaseModel):
    """Parsed content chunk."""
    
    chunk_id: str = Field(..., description="Unique chunk identifier")
    file_id: str = Field(..., description="Parent file identifier")
    session_id: str = Field(..., description="Session identifier")
    
    # Content type
    type: str = Field(..., description="paragraph|heading|table|image")
    content: str = Field(..., description="Extracted text content")
    markdown: Optional[str] = Field(None, description="Markdown formatted")
    table_json: Optional[str] = Field(None, description="JSON table data")
    
    # Location
    page: Optional[int] = Field(None, description="Page number (1-based)")
    index: int = Field(default=1, description="Chunk index within page")
    row_range: Optional[str] = Field(None, description="Row range e.g., '1-10'")
    
    # Metadata
    source: str = Field(..., description="pymupdf|ocr|pandas|openpyxl|python-docx")
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    content_hash: str = Field(..., description="SHA256 hash for deduplication")
    
    # Image specific
    bbox: Optional[List[float]] = Field(None, description="Bounding box [x0,y0,x1,y1]")
    
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    
    model_config = {"populate_by_name": True}
```

### 2.3 Retrieval Request/Response

```python
class RetrievalRequest(BaseModel):
    """Request for chunk retrieval."""
    session_id: str
    query: str = Field(..., description="User query")
    top_k: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=4000, ge=100, le=16000)
    file_ids: Optional[List[str]] = Field(None, description="Filter by specific files")
    include_images: bool = Field(default=False)
    mode: str = Field(default="auto", description="auto|explicit")


class RetrievalResult(BaseModel):
    """Retrieval result with context."""
    chunks: List[Chunk]
    total_chunks: int
    estimated_tokens: int
    budget_status: str  # direct|top-k|summarize|error
    citations: List[dict] = Field(default_factory=list)
```

---

## 3. Storage Layer

### 3.1 Directory Structure

```
~/.efp/workspace/file_context/
├── sessions/
│   ├── {session_id}.json      # Session file metadata
│   └── ...
├── chunks/
│   ├── {file_id}/
│   │   ├── {chunk_id}.json   # Individual chunk data
│   │   └── ...
│   └── ...
└── index/
    ├── keyword/              # Keyword inverted index
    │   └── {term}.json
    └── embeddings/            # Vector embeddings (future)
        └── {chunk_id}.npy
```

### 3.2 Storage Implementation

**File:** `src/hooks/file_context/storage.py`

```python
import json
import os
import hashlib
from pathlib import Path
from typing import List, Optional
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
    
    # ============ Chunk Methods ============
    
    def _chunk_path(self, file_id: str, chunk_id: str) -> Path:
        file_dir = self.chunks_dir / file_id
        file_dir.mkdir(parents=True, exist_ok=True)
        return file_dir / f"{chunk_id}.json"
    
    def save_chunk(self, chunk: Chunk) -> None:
        """Save chunk to storage."""
        path = self._chunk_path(chunk.file_id, chunk.chunk_id)
        path.write_text(chunk.model_dump_json(indent=2))
    
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
    
    # ============ Utility Methods ============
    
    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Compute SHA256 hash for deduplication."""
        return hashlib.sha256(content.encode()).hexdigest()


# Global instance
storage = FileContextStorage()
```

---

## 4. Retrieval System

### 4.1 Keyword Index

**File:** `src/hooks/file_context/retrieval.py`

```python
import re
import json
from collections import defaultdict
from typing import List, Dict, Set, Tuple
from pathlib import Path
from .models import Chunk, RetrievalRequest, RetrievalResult
from .storage import storage


class KeywordIndex:
    """Simple inverted keyword index."""
    
    def __init__(self):
        self.index: Dict[str, Set[str]] = defaultdict(set)  # term -> chunk_ids
        self.chunk_terms: Dict[str, Set[str]] = {}  # chunk_id -> terms
    
    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into terms."""
        # Simple tokenization: lowercase, alphanumeric
        text = text.lower()
        tokens = re.findall(r'\b[a-z0-9]+\b', text)
        # Filter stopwords
        stopwords = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'to', 'of', 'in', 'for'}
        return [t for t in tokens if t not in stopwords and len(t) > 2]
    
    def add_chunk(self, chunk: Chunk) -> None:
        """Add chunk to index."""
        terms = self._tokenize(chunk.content)
        self.chunk_terms[chunk.chunk_id] = set(terms)
        
        for term in terms:
            self.index[term].add(chunk.chunk_id)
    
    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """Search index and return top matching chunk_ids with scores."""
        query_terms = self._tokenize(query)
        
        # Count term matches
        scores: Dict[str, float] = defaultdict(float)
        for term in query_terms:
            if term in self.index:
                for chunk_id in self.index[term]:
                    scores[chunk_id] += 1.0 / len(query_terms)
        
        # Sort by score
        sorted_chunks = sorted(scores.items(), key=lambda x: -x[1])
        return sorted_chunks[:top_k]


class RetrievalEngine:
    """Hybrid retrieval engine."""
    
    def __init__(self):
        self.keyword_index = KeywordIndex()
        self._index_loaded = False
    
    def _ensure_index(self, session_id: str) -> None:
        """Load or rebuild index for session."""
        if self._index_loaded:
            return
        
        chunks = storage.get_session_chunks(session_id)
        for chunk in chunks:
            self.keyword_index.add_chunk(chunk)
        
        self._index_loaded = True
    
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Perform retrieval with budget control."""
        self._ensure_index(request.session_id)
        
        # Get candidate chunks
        if request.file_ids:
            all_chunks = []
            for file_id in request.file_ids:
                all_chunks.extend(storage.get_file_chunks(file_id))
        else:
            all_chunks = storage.get_session_chunks(request.session_id)
        
        # Keyword search
        keyword_results = self.keyword_index.search(request.query, top_k=request.top_k * 2)
        
        # Get top-k chunks
        chunk_ids = [cid for cid, _ in keyword_results[:request.top_k]]
        chunks = []
        for chunk in all_chunks:
            if chunk.chunk_id in chunk_ids:
                chunks.append(chunk)
        
        # Estimate tokens
        estimated_tokens = sum(len(c.content) // 4 for c in chunks)  # Rough estimate
        
        # Determine budget status
        if estimated_tokens < 1000:
            budget_status = "direct"
        elif estimated_tokens < 4000:
            budget_status = "top-k"
        elif estimated_tokens < 8000:
            budget_status = "summarize"
        else:
            budget_status = "error"
        
        # Build citations
        citations = [
            {
                "chunk_id": c.chunk_id,
                "file_id": c.file_id,
                "page": c.page,
                "type": c.type,
                "preview": c.content[:100] + "..."
            }
            for c in chunks
        ]
        
        return RetrievalResult(
            chunks=chunks,
            total_chunks=len(chunks),
            estimated_tokens=estimated_tokens,
            budget_status=budget_status,
            citations=citations
        )
```

### 4.3 Image Chunk Retrieval

- **Default**: `include_images = False` (text chunks only)
- **When enabled**:
  - Use OCR text content for retrieval
  - Store image metadata separately
  - Option for LLM-generated captions (if `generate_captions: true`)

### 4.4 Multi-File Ranking

When retrieving across multiple files:
1. **Relevance Score**: Primary sort by keyword/semantic match
2. **File Priority**: 
   - Explicitly referenced files get highest priority
   - `@last` referenced files: recent files boosted
   - `@all`: equal weight
3. **Deduplication**: Use `content_hash` to avoid duplicate content

---

## 5. AI Context Injection

### 5.1 Context Builder

```python
def build_rag_prompt(
    user_message: str,
    retrieval_result: RetrievalResult,
    budget_status: str
) -> Tuple[str, str]:
    """Build prompt with retrieved context."""
    
    if budget_status == "error":
        return "", "error:query_too_broad"
    
    # Build context from chunks
    context_parts = []
    for i, chunk in enumerate(retrieval_result.chunks, 1):
        source_info = f"[{chunk.file_id}"
        if chunk.page:
            source_info += f", page {chunk.page}"
        source_info += "]"
        
        context_parts.append(f"--- Context {i} {source_info} ---\n{chunk.content}")
    
    context_text = "\n\n".join(context_parts)
    
    # Build prompt
    if budget_status == "direct":
        prompt = f"""Based on the following context, answer the user's question.

Context:
{context_text}

Question: {user_message}

Answer:"""
    elif budget_status == "top-k":
        prompt = f"""Based on the following context (top-k relevant excerpts), answer the user's question.

Context:
{context_text}

Question: {user_message}

Answer:"""
    elif budget_status == "summarize":
        prompt = f"""The relevant context is too large. First summarize the context briefly, then answer the question.

Context:
{context_text}

Question: {user_message}

First, provide a brief summary of the relevant context, then answer:"""
    else:
        prompt = f"""Question: {user_message}

Answer:"""
    
    return prompt, budget_status
```

### 5.2 Citation Formatter

```python
def format_citations(chunks: List[Chunk]) -> str:
    """Format citations for display."""
    if not chunks:
        return ""
    
    citations = []
    for chunk in chunks:
        if chunk.page:
            citations.append(f"[{chunk.filename}, page {chunk.page}]")
        else:
            citations.append(f"[{chunk.filename}]")
    
    return "Sources: " + ", ".join(citations)
```

---

## 6. Command Parser

### 6.1 Parser Implementation

```python
import re
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class FileReference:
    """Parsed file reference."""
    type: str  # file, last, all, chunk
    value: Optional[str] = None  # file_id or chunk_id


class CommandParser:
    """Parse file reference commands."""
    
    PATTERN = r'@(file_[a-zA-Z0-9]+|last|all|chunk_[a-zA-Z0-9]+)'
    
    @classmethod
    def parse(cls, text: str) -> Tuple[str, List[FileReference]]:
        """Parse command references from text.
        
        Returns:
            (cleaned_message, list of references)
        """
        references = []
        
        for match in re.finditer(cls.PATTERN, text):
            ref_text = match.group(1)
            
            if ref_text.startswith('file_'):
                ref = FileReference(type='file', value=ref_text)
            elif ref_text == 'last':
                ref = FileReference(type='last')
            elif ref_text == 'all':
                ref = FileReference(type='all')
            elif ref_text.startswith('chunk_'):
                ref = FileReference(type='chunk', value=ref_text)
            else:
                continue
            
            references.append(ref)
        
        # Remove command syntax from message
        cleaned = re.sub(cls.PATTERN, '', text).strip()
        
        return cleaned, references
    
    @classmethod
    def resolve_references(
        cls,
        references: List[FileReference],
        session_files: List[SessionFileMeta]
    ) -> List[str]:
        """Resolve references to file_ids.
        
        Priority: chunk > file > last > all
        """
        file_ids = set()
        
        # Process in priority order
        ref_by_type = {
            'chunk': [],
            'file': [],
            'last': [],
            'all': []
        }
        
        for ref in references:
            ref_by_type[ref.type].append(ref)
        
        # Chunk references (highest priority)
        if ref_by_type['chunk']:
            # Return empty - chunk IDs need direct lookup
            return []
        
        # File references
        for ref in ref_by_type['file']:
            file_ids.add(ref.value)
        
        # Last reference
        if ref_by_type['last'] and session_files:
            last_file = session_files[-1]
            file_ids.add(last_file.file_id)
        
        # All references
        if ref_by_type['all']:
            file_ids.update(f.file_id for f in session_files)
        
        return list(file_ids)
```

---

## 7. API Reference

### 7.1 Context Injection API

**Endpoint:** `POST /api/context/inject`

```http
POST /api/context/inject
Content-Type: application/json
X-Session-ID: {session_id}

{
    "message": "What is this document about?",
    "mode": "auto",          // auto|explicit
    "top_k": 5,
    "max_tokens": 4000,
    "include_images": false
}
```

**Response:**
```json
{
    "success": true,
    "prompt": "Based on the following context...",
    "budget_status": "top-k",
    "chunks": [...],
    "citations": [
        {"chunk_id": "xxx", "file_id": "xxx", "page": 1}
    ],
    "estimated_tokens": 2500
}
```

### 7.2 Chunk Search API

**Endpoint:** `GET /api/chunks/search`

```http
GET /api/chunks/search?session_id=xxx&query=revenue&top_k=5
```

**Response:**
```json
{
    "success": true,
    "chunks": [
        {
            "chunk_id": "file123_pdf_1_001",
            "file_id": "file123",
            "content": "Revenue increased by 15%...",
            "page": 1,
            "type": "paragraph"
        }
    ],
    "total": 42
}
```

### 7.3 Session Files API

**Endpoint:** `GET /api/context/files`

```http
GET /api/context/files?session_id=xxx
```

**Response:**
```json
{
    "success": true,
    "files": [
        {
            "file_id": "file123",
            "filename": "report.pdf",
            "content_type": "application/pdf",
            "parse_status": "completed",
            "chunk_count": 42,
            "parsed_at": "2026-03-01T12:00:00Z"
        }
    ]
}
```

### 7.4 Citation Audit API

**Endpoint:** `POST /api/citations/log`

```http
POST /api/citations/log
Content-Type: application/json

{
    "session_id": "xxx",
    "file_id": "file123",
    "chunk_id": "file123_pdf_1_001",
    "action": "view"
}
```

---

## 8. Database Schema

### 8.1 Session Context (JSON)

```json
// ~/.efp/workspace/file_context/sessions/{session_id}.json
{
    "session_id": "webchat_20260301_120000",
    "files": [
        {
            "file_id": "file_abc123",
            "session_id": "webchat_20260301_120000",
            "filename": "quarterly_report.pdf",
            "content_type": "application/pdf",
            "parse_status": "completed",
            "chunk_count": 42,
            "total_chars": 15000,
            "created_at": "2026-03-01T12:00:00Z",
            "parsed_at": "2026-03-01T12:00:05Z"
        }
    ],
    "updated_at": "2026-03-01T12:00:05Z"
}
```

### 8.2 Chunk (JSON)

```json
// ~/.efp/workspace/file_context/chunks/file_abc123/file_abc123_pdf_1_001.json
{
    "chunk_id": "file_abc123_pdf_1_001",
    "file_id": "file_abc123",
    "session_id": "webchat_20260301_120000",
    "type": "paragraph",
    "content": "The quarterly revenue increased by 15% compared to the previous quarter...",
    "markdown": "The quarterly **revenue** increased by 15%...",
    "page": 1,
    "index": 1,
    "source": "pymupdf",
    "confidence": 0.98,
    "content_hash": "a1b2c3d4...",
    "created_at": "2026-03-01T12:00:05Z"
}
```

---

## 9. Security & Privacy

### 9.1 Session Isolation
- All APIs require valid `session_id` in header or query
- Files are strictly isolated to their owning session
- Cross-session access returns 403 Forbidden

### 9.2 Rate Limiting
- Context injection API: 60 requests/minute per session
- File parse API: 10 requests/minute per session

### 9.3 Input Validation
- Sanitize all user input in prompts
- Escape markdown in chunk content
- Limit citation preview length (max 500 chars)

### 9.4 Audit Logging
- All citation actions logged with timestamp
- Log format: `{session_id, user_id, file_id, action, timestamp}`

---

## 10. Testing Strategy

### 9.1 Unit Tests

**File:** `tests/test_file_context.py`

```python
import pytest
from src.hooks.file_context.models import SessionFileMeta, Chunk
from src.hooks.file_context.storage import FileContextStorage
from src.hooks.file_context.retrieval import KeywordIndex, RetrievalEngine
from src.hooks.file_context.parser import CommandParser


class TestStorage:
    def test_save_and_load_session(self, tmp_path):
        storage = FileContextStorage(str(tmp_path))
        
        meta = SessionFileMeta(
            file_id="test123",
            session_id="sess456",
            filename="test.pdf",
            content_type="application/pdf"
        )
        
        storage.add_file_to_session("sess456", meta)
        
        files = storage.get_session_files("sess456")
        assert len(files) == 1
        assert files[0].file_id == "test123"


class TestKeywordIndex:
    def test_tokenize(self):
        index = KeywordIndex()
        tokens = index._tokenize("The revenue increased")
        assert "revenue" in tokens
        assert "the" not in tokens
    
    def test_search(self):
        index = KeywordIndex()
        
        chunk = Chunk(
            chunk_id="c1",
            file_id="f1",
            session_id="s1",
            type="paragraph",
            content="Revenue increased by 15%",
            source="test",
            content_hash="abc"
        )
        index.add_chunk(chunk)
        
        results = index.search("revenue", top_k=1)
        assert results[0][0] == "c1"


class TestCommandParser:
    def test_parse_file_reference(self):
        msg, refs = CommandParser.parse("What is @file_abc123 about?")
        assert msg == "What is about?"
        assert len(refs) == 1
        assert refs[0].type == "file"
        assert refs[0].value == "file_abc123"
    
    def test_parse_multiple(self):
        msg, refs = CommandParser.parse("Compare @file_a and @file_b")
        assert len(refs) == 2
    
    def test_parse_last(self):
        msg, refs = CommandParser.parse("Summarize @last")
        assert refs[0].type == "last"
    
    def test_resolve_references(self):
        refs = [
            FileReference(type="file", value="file1"),
            FileReference(type="last")
        ]
        
        session_files = [
            SessionFileMeta(file_id="file0", session_id="s1", filename="a.pdf", content_type="application/pdf"),
            SessionFileMeta(file_id="file1", session_id="s1", filename="b.pdf", content_type="application/pdf"),
        ]
        
        resolved = CommandParser.resolve_references(refs, session_files)
        assert "file1" in resolved
        assert "file0" in resolved  # from @last
```

### 9.2 Integration Tests

```python
class TestFileAIIntegration:
    """End-to-end integration tests."""
    
    @pytest.mark.asyncio
    async def test_upload_parse_chat_flow(self, client):
        # 1. Upload file
        files = {"file": ("test.pdf", b"%PDF-...", "application/pdf")}
        response = await client.post(
            "/api/files/upload?session_id=test123",
            data=files
        )
        assert response.json()["success"]
        file_id = response.json()["file_id"]
        
        # 2. Parse file
        response = await client.post(
            "/api/files/parse",
            json={"file_id": file_id},
            headers={"X-Session-ID": "test123"}
        )
        assert response.json()["success"]
        
        # 3. Ask about file
        response = await client.post(
            "/api/context/inject",
            json={
                "message": "What is this document about?",
                "session_id": "test123"
            }
        )
        result = response.json()
        assert result["success"]
        assert result["budget_status"] in ["direct", "top-k"]
        assert len(result["chunks"]) > 0
    
    @pytest.mark.asyncio
    async def test_command_references(self, client):
        # Upload two files
        # ...
        
        # Test @file reference
        response = await client.post(
            "/api/context/inject",
            json={
                "message": "Summarize @file_abc",
                "session_id": "test123"
            }
        )
        # Verify only file_abc chunks are used
```

### 9.3 Performance Tests

```python
class TestRetrievalPerformance:
    def test_large_document_retrieval(self):
        """Test retrieval with 50+ page PDF."""
        # Setup large document
        # ...
        
        engine = RetrievalEngine()
        
        import time
        start = time.time()
        result = engine.retrieve(RetrievalRequest(
            session_id="test",
            query="revenue",
            top_k=10
        ))
        elapsed = time.time() - start
        
        assert elapsed < 1.0  # Should complete in under 1 second
        assert result.estimated_tokens < 8000
```

---

## 10. Implementation Checklist

### Phase 1: Storage Infrastructure
- [ ] Create `src/hooks/file_context/__init__.py`
- [ ] Create `src/hooks/file_context/models.py`
- [ ] Create `src/hooks/file_context/storage.py`
- [ ] Implement SessionContext CRUD operations
- [ ] Implement Chunk storage
- [ ] Unit tests for storage layer

### Phase 2: Retrieval System
- [ ] Create `src/hooks/file_context/retrieval.py`
- [ ] Implement KeywordIndex
- [ ] Implement RetrievalEngine
- [ ] Add token estimation
- [ ] Add budget status determination
- [ ] Unit tests for retrieval

### Phase 3: AI Integration
- [ ] Create `src/hooks/file_context/parser.py`
- [ ] Implement CommandParser
- [ ] Add file reference resolution
- [ ] Implement RAG prompt builder
- [ ] Add citation formatting
- [ ] Integrate with chat handler

### Phase 4: API & UI
- [ ] Add `/api/context/inject` endpoint
- [ ] Add `/api/chunks/search` endpoint
- [ ] Add `/api/context/files` endpoint
- [ ] Add file list to sidebar
- [ ] Add parse status indicators
- [ ] Add citation click handling

---

## Appendix: Error Codes

| Code | Meaning | Action |
|------|---------|--------|
| E001 | File not found | Return available files |
| E002 | Parse pending | Return progress, retry after |
| E003 | Parse failed | Offer re-parse |
| E004 | Query too broad | Suggest narrowing |
| E005 | No relevant chunks | Suggest @all |

---

**Document Version:** 1.0  
**Last Updated:** 2026-03-01  
**Related:** `docs/file-ai-integration.md`
