"""File parser data models."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Block(BaseModel):
    """A structured block extracted from a file.
    
    Schema Contract:
    - chunk_id: {file_id}_{page}_{row} format, globally unique
    - page: 1-based (PDF, Word)
    - row_range: 1-based, closed interval (e.g., "1-10")
    - confidence: 0.0 - 1.0 (OCR default 0.8, Vision default 0.9)
    """
    chunk_id: str = Field(..., description="Unique block ID: {file_id}_{page}_{row}")
    type: str = Field(..., description="Type: heading, paragraph, table, list, image")
    content: str = Field(..., description="Text content")
    
    # Heading specific
    level: Optional[int] = Field(None, ge=1, le=6, description="Heading level 1-6")
    
    # Table specific
    markdown: Optional[str] = None
    table_json: Optional[Any] = Field(None, alias="json")  # Use alias for JSON field
    
    # Location
    page: Optional[int] = Field(None, ge=1, description="Page number, 1-based")
    sheet: Optional[str] = Field(None, description="Excel sheet name")
    row_range: Optional[str] = Field(None, description="Row range, 1-based closed interval")
    bbox: Optional[List[List[int]]] = Field(None, description="Bounding box [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]")
    
    # Metadata
    method: str = Field(..., description="Extraction method: pymupdf, pandas, vision, ocr, paddleocr, tesseract")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    extracted_at: str = Field(..., description="ISO timestamp")
    
    class Config:
        populate_by_name = True


class ParseResult(BaseModel):
    """Result of file parsing."""
    success: bool
    content_type: str = Field(..., description="MIME type: image/jpeg, application/pdf, etc.")
    file_id: str = Field(..., description="UUID of the file")
    filename: str
    
    # Content
    markdown: str = ""
    blocks: List[Block] = Field(default_factory=list)
    
    # Summary
    json: Dict[str, Any] = Field(default_factory=dict)
    
    # Metadata
    parse_time_ms: int = 0
    error: Optional[str] = None


class ImageConstraints(BaseModel):
    """Constraints for sending images to LLM."""
    max_count: int = Field(1, description="Max images per LLM request")
    max_size_mb: int = Field(3, description="Max file size in MB")
    max_dimension: int = Field(1024, description="Max width or height in pixels for compression")
    jpeg_quality: int = Field(80, ge=70, le=85, description="JPEG quality for compression")
    allowed_formats: List[str] = Field(
        default_factory=lambda: ["jpg", "jpeg", "png", "webp", "gif"],
        description="Allowed image formats"
    )


class FileMetadata(BaseModel):
    """File metadata stored in memory/Redis."""
    file_id: str
    original_filename: str = Field(..., description="User's original filename")
    stored_filename: str = Field(..., description="Server-side canonical filename")
    content_type: str
    size: int = Field(..., description="File size in bytes")
    uploaded_at: str = Field(..., description="ISO timestamp")
    session_id: Optional[str] = None


class FileValidationError(Exception):
    """Base file validation error."""
    def __init__(self, message: str, error_type: str):
        super().__init__(message)
        self.error_type = error_type


class FileTooLargeError(FileValidationError):
    """File exceeds size limit."""
    def __init__(self, message: str):
        super().__init__(message, "file_too_large")


class UnsupportedFileTypeError(FileValidationError):
    """File type not allowed."""
    def __init__(self, message: str):
        super().__init__(message, "unsupported_file_type")


class FileNotFoundError(FileValidationError):
    """File not found."""
    def __init__(self, message: str):
        super().__init__(message, "file_not_found")


class ParseError(Exception):
    """Failed to parse file."""
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(message)
        self.details = details or {}
