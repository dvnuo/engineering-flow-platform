"""File context data models."""

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
