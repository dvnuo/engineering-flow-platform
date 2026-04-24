from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ArtifactRecord(BaseModel):
    artifact_id: str
    file_id: str
    source_type: str
    source_kind: str
    source_locator: Optional[str] = None
    filename: str
    content_type: str
    size: int
    session_id: Optional[str] = None
    parse_status: str = "pending"
    parse_error: Optional[str] = None
    projection_kind: Optional[str] = None
    preview: Optional[str] = None
    chunk_count: int = 0
    total_chars: int = 0
    text_ref: Optional[str] = None
    context_ref: Optional[str] = None
    digest_ref: Optional[str] = None
    full_markdown_chars: int = 0
    provider_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class ArtifactBinding(BaseModel):
    artifact_id: str
    scope_type: str
    scope_id: str
    role: str = "attachment"
