"""Tests for chunking module."""

import pytest
from src.memory.chunking import chunk_markdown, Chunk, extract_heading


class TestChunkMarkdown:
    """Tests for chunk_markdown function."""
    
    def test_empty_text_returns_empty_list(self):
        """Empty text should return empty list."""
        result = chunk_markdown("", "PROJECT.md")
        assert result == []
    
    def test_whitespace_only_returns_empty_list(self):
        """Whitespace-only text should return empty list."""
        result = chunk_markdown("   \n\n   ", "PROJECT.md")
        assert result == []
    
    def test_no_headings_chunks_entire_text(self):
        """Text without headings should be treated as single chunk."""
        text = "This is a short piece of text without any headings."
        result = chunk_markdown(text, "PROJECT.md")
        
        assert len(result) == 1
        assert result[0].text == text
        assert result[0].meta["source"] == "PROJECT.md"
        assert result[0].meta["heading"] == ""
    
    def test_single_heading(self):
        """Single heading should create one section."""
        text = """# Main Heading

This is some content under the main heading.
It has multiple lines of content.
"""
        result = chunk_markdown(text, "PROJECT.md")
        
        assert len(result) == 1
        assert "Main Heading" in result[0].meta["heading"]
        assert "content under the main heading" in result[0].text
    
    def test_multiple_headings_creates_multiple_chunks(self):
        """Multiple headings should create separate chunks."""
        text = """# Section One

Content for section one.

## Section Two

Content for section two.

### Section Three

Content for section three.
"""
        result = chunk_markdown(text, "PROJECT.md")
        
        assert len(result) == 3
        assert "Section One" in result[0].meta["heading"]
        assert "Section Two" in result[1].meta["heading"]
        assert "Section Three" in result[2].meta["heading"]
    
    def test_long_section_splits_by_blank_lines(self):
        """Sections exceeding max_chars should split by blank lines."""
        # Create text longer than max_chars
        long_paragraph = "A" * 600 + ". "
        text = f"""# Heading

{long_paragraph}
{long_paragraph}
{long_paragraph}
"""
        result = chunk_markdown(text, "PROJECT.md", max_chars=1200)
        
        # Should have multiple chunks
        assert len(result) > 1
    
    def test_dated_document_kind(self):
        """Dated documents should use the dated kind and include date."""
        text = """# Work Log

Some dated content.
"""
        result = chunk_markdown(text, "2026-03-03.md", kind="dated", date="2026-03-03")
        
        assert len(result) == 1
        assert result[0].meta["kind"] == "dated"
        assert result[0].meta["date"] == "2026-03-03"
        assert result[0].id.startswith("dated:")
    
    def test_chunk_id_format(self):
        """Chunk IDs should follow the expected format."""
        text = """## My Heading

Content here.
"""
        result = chunk_markdown(text, "PROJECT.md")
        
        assert len(result) == 1
        chunk_id = result[0].id
        assert "doc:PROJECT.md#" in chunk_id
        assert "my-heading" in chunk_id
        # Chunk number should be present (e.g., -01)
        assert "-01" in chunk_id
    
    def test_chunk_metadata_complete(self):
        """Chunks should have complete metadata."""
        text = """## Test Section

Test content.
"""
        result = chunk_markdown(text, "TEST.md")
        
        assert len(result) == 1
        meta = result[0].meta
        assert "source" in meta
        assert "heading" in meta
        assert "kind" in meta
        assert meta["source"] == "TEST.md"
        assert meta["heading"] == "Test Section"
        assert meta["kind"] == "core"
    
    def test_custom_max_chars(self):
        """Custom max_chars should be respected."""
        text = "# Heading\n\n" + "A" * 500 + "\n\n" + "B" * 500
        result = chunk_markdown(text, "TEST.md", max_chars=300)
        
        # With max_chars=300, should split into multiple chunks
        assert len(result) >= 2
    
    def test_small_content_still_creates_chunk(self):
        """Small content creates a chunk (that's okay, min_chars applies to splits)."""
        text = "# Heading\n\nShort."
        result = chunk_markdown(text, "TEST.md", max_chars=1200, min_chars=100)
        
        # Small content still creates a chunk (heading makes it valid)
        # The min_chars check applies when splitting, not for initial chunk
        assert len(result) >= 1


class TestExtractHeading:
    """Tests for extract_heading function."""
    
    def test_extract_h1(self):
        """Should extract h1 heading."""
        text = "# My Heading\n\nContent"
        assert extract_heading(text) == "My Heading"
    
    def test_extract_h2(self):
        """Should extract h2 heading."""
        text = "## Second Level\n\nContent"
        assert extract_heading(text) == "Second Level"
    
    def test_extract_h3(self):
        """Should extract h3 heading."""
        text = "### Third Level\n\nContent"
        assert extract_heading(text) == "Third Level"
    
    def test_no_heading_returns_empty(self):
        """Text without heading should return empty string."""
        text = "Just some content without any heading."
        assert extract_heading(text) == ""
    
    def test_strips_whitespace(self):
        """Should strip whitespace from heading."""
        text = "#   Spaced Heading   \n\nContent"
        assert extract_heading(text) == "Spaced Heading"


class TestChunkDataclass:
    """Tests for Chunk dataclass."""
    
    def test_chunk_creation(self):
        """Should create chunk with all fields."""
        chunk = Chunk(
            id="test:123",
            text="Some content",
            meta={"source": "test.md", "heading": "Test"}
        )
        
        assert chunk.id == "test:123"
        assert chunk.text == "Some content"
        assert chunk.meta["source"] == "test.md"
    
    def test_chunk_repr(self):
        """Chunk repr should show truncated text."""
        chunk = Chunk(
            id="test:123",
            text="A" * 100,
            meta={}
        )
        
        repr_str = repr(chunk)
        assert "test:123" in repr_str
        assert "..." in repr_str
