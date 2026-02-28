"""Tests for file parser models."""

import pytest
from datetime import datetime

from src.utils.file_parser.models import (
    Block,
    ParseResult,
    ImageConstraints,
    FileMetadata,
    FileValidationError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)


class TestBlock:
    """Test Block model."""
    
    def test_block_creation(self):
        """Test Block creation with required fields."""
        block = Block(
            chunk_id="test_1",
            type="paragraph",
            content="Test content",
            method="test",
            extracted_at="2026-02-28T00:00:00Z"
        )
        
        assert block.chunk_id == "test_1"
        assert block.type == "paragraph"
        assert block.content == "Test content"
        assert block.method == "test"
    
    def test_block_with_location(self):
        """Test Block with location data."""
        block = Block(
            chunk_id="pdf_1_5",
            type="paragraph",
            content="Page 5 content",
            page=5,
            method="pymupdf",
            confidence=0.95,
            extracted_at="2026-02-28T00:00:00Z"
        )
        
        assert block.page == 5
        assert block.chunk_id == "pdf_1_5"
        assert block.confidence == 0.95
    
    def test_block_with_bbox(self):
        """Test Block with bounding box."""
        block = Block(
            chunk_id="img_1_1",
            type="paragraph",
            content="Text in image",
            bbox=[[10, 20], [50, 20], [50, 40], [10, 40]],
            method="paddleocr",
            confidence=0.9,
            extracted_at="2026-02-28T00:00:00Z"
        )
        
        assert block.bbox == [[10, 20], [50, 20], [50, 40], [10, 40]]
    
    def test_block_with_table(self):
        """Test Block for table."""
        block = Block(
            chunk_id="pdf_1_table_1",
            type="table",
            content="",
            markdown="| A | B |\n|---|---|\n| 1 | 2 |",
            json=[["A", "B"], ["1", "2"]],
            page=1,
            method="pdfplumber",
            confidence=0.9,
            extracted_at="2026-02-28T00:00:00Z"
        )
        
        assert block.type == "table"
        assert block.markdown is not None
        assert block.table_json is not None
    
    def test_block_validation(self):
        """Test Block field validation."""
        # confidence out of range
        with pytest.raises(Exception):
            Block(
                chunk_id="test",
                type="paragraph",
                content="test",
                method="test",
                confidence=1.5,  # Should be 0-1
                extracted_at="2026-02-28T00:00:00Z"
            )


class TestParseResult:
    """Test ParseResult model."""
    
    def test_parse_result_success(self):
        """Test successful ParseResult."""
        result = ParseResult(
            success=True,
            content_type="application/pdf",
            file_id="abc123",
            filename="test.pdf",
            markdown="# Test\nContent",
            parse_time_ms=1500
        )
        
        assert result.success is True
        assert result.content_type == "application/pdf"
        assert result.parse_time_ms == 1500
    
    def test_parse_result_error(self):
        """Test error ParseResult."""
        result = ParseResult(
            success=False,
            content_type="application/pdf",
            file_id="abc123",
            filename="test.pdf",
            error="Parse failed"
        )
        
        assert result.success is False
        assert result.error == "Parse failed"


class TestImageConstraints:
    """Test ImageConstraints model."""
    
    def test_defaults(self):
        """Test default values."""
        constraints = ImageConstraints()
        
        assert constraints.max_count == 1
        assert constraints.max_size_mb == 3
        assert constraints.max_dimension == 1024
        assert constraints.jpeg_quality == 80
        assert "jpg" in constraints.allowed_formats
    
    def test_custom_values(self):
        """Test custom values."""
        constraints = ImageConstraints(
            max_count=2,
            max_size_mb=5,
            max_dimension=2048,
            jpeg_quality=85
        )
        
        assert constraints.max_count == 2
        assert constraints.max_size_mb == 5
        assert constraints.max_dimension == 2048
        assert constraints.jpeg_quality == 85
    
    def test_quality_validation(self):
        """Test quality bounds."""
        with pytest.raises(Exception):
            ImageConstraints(jpeg_quality=100)  # Too high


class TestFileMetadata:
    """Test FileMetadata model."""
    
    def test_creation(self):
        """Test FileMetadata creation."""
        metadata = FileMetadata(
            file_id="abc123",
            original_filename="document.pdf",
            stored_filename="abc123.pdf",
            content_type="application/pdf",
            size=1024000,
            uploaded_at="2026-02-28T00:00:00Z"
        )
        
        assert metadata.file_id == "abc123"
        assert metadata.original_filename == "document.pdf"
        assert metadata.size == 1024000
    
    def test_with_session(self):
        """Test with session ID."""
        metadata = FileMetadata(
            file_id="abc123",
            original_filename="doc.pdf",
            stored_filename="abc123.pdf",
            content_type="application/pdf",
            size=1024,
            uploaded_at="2026-02-28T00:00:00Z",
            session_id="session-456"
        )
        
        assert metadata.session_id == "session-456"


class TestExceptions:
    """Test exception classes."""
    
    def test_file_too_large(self):
        """Test FileTooLargeError."""
        error = FileTooLargeError("File too large")
        
        assert str(error) == "File too large"
        assert error.error_type == "file_too_large"
    
    def test_unsupported_file_type(self):
        """Test UnsupportedFileTypeError."""
        error = UnsupportedFileTypeError("Type not allowed")
        
        assert str(error) == "Type not allowed"
        assert error.error_type == "unsupported_file_type"
