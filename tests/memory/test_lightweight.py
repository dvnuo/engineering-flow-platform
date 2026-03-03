"""Tests for LightweightMemory upgrades (Step B)."""

import json
import os
import tempfile
import pytest
from pathlib import Path
from src.memory.lightweight import LightweightMemory


class TestLightweightMemoryUpsert:
    """Tests for upsert functionality."""
    
    def test_upsert_new_entry(self):
        """Upsert should add new entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            mem.upsert("test1", "Test content", {"source": "test.md"})
            
            entry = mem.get_entry("test1")
            assert entry is not None
            assert entry["content"] == "Test content"
            assert entry["meta"]["source"] == "test.md"
    
    def test_upsert_existing_entry(self):
        """Upsert should update existing entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            mem.upsert("test1", "Original content", {"source": "test.md"})
            
            # Update the entry
            mem.upsert("test1", "Updated content", {"source": "new.md"})
            
            entry = mem.get_entry("test1")
            assert entry["content"] == "Updated content"
            assert entry["meta"]["source"] == "new.md"
            # created_at should be preserved
            assert entry["created_at"] != ""
    
    def test_upsert_with_mtime(self):
        """Upsert should store mtime."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            mem.upsert("test1", "Content", mtime=1234567890.0)
            
            entry = mem.get_entry("test1")
            assert entry["mtime"] == 1234567890.0


class TestLightweightMemoryDelete:
    """Tests for delete functionality."""
    
    def test_delete_existing(self):
        """Delete should remove existing entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            mem.upsert("test1", "Content")
            
            result = mem.delete("test1")
            assert result is True
            assert mem.get_entry("test1") is None
    
    def test_delete_nonexistent(self):
        """Delete should return False for nonexistent entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            
            result = mem.delete("nonexistent")
            assert result is False
    
    def test_delete_by_source(self):
        """Delete by source should remove all entries from that source."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            mem.upsert("chunk1", "Content 1", {"source": "file1.md"})
            mem.upsert("chunk2", "Content 2", {"source": "file1.md"})
            mem.upsert("chunk3", "Content 3", {"source": "file2.md"})
            
            count = mem.delete_by_source("file1.md")
            
            assert count == 2
            assert mem.get_entry("chunk1") is None
            assert mem.get_entry("chunk2") is None
            assert mem.get_entry("chunk3") is not None


class TestLightweightMemorySearch:
    """Tests for search functionality."""
    
    def test_search_returns_full_content(self):
        """Search should return full chunk content, not preview."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir, score_threshold=0.01)
            # Add a long entry with distinguishable content
            long_content = "uniquekeyword " * 500
            mem.upsert("long", long_content, {"source": "test.md"})
            
            results = mem.search("uniquekeyword")
            
            assert len(results) == 1
            # Should return full content, not truncated
            assert len(results[0]["content"]) == len(long_content)
    
    def test_search_returns_meta(self):
        """Search should return metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            mem.upsert("test1", "Test content", {"source": "test.md", "heading": "Test"})
            
            results = mem.search("content")
            
            assert len(results) == 1
            assert results[0]["meta"]["source"] == "test.md"
            assert results[0]["meta"]["heading"] == "Test"
    
    def test_search_returns_id(self):
        """Search should return entry id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            mem.upsert("my-chunk-id", "Test content")
            
            results = mem.search("content")
            
            assert len(results) == 1
            assert results[0]["id"] == "my-chunk-id"


class TestLightweightMemorySchema:
    """Tests for schema migration."""
    
    def test_v1_migration(self):
        """Should migrate from v1 schema."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create old v1 index
            index_file = Path(tmpdir) / "index.json"
            v1_data = {
                "entry1": "Content 1",
                "entry2": {"content": "Content 2", "metadata": {"source": "test.md"}},
            }
            with open(index_file, 'w') as f:
                json.dump(v1_data, f)
            
            # Load with new version
            mem = LightweightMemory(storage_dir=tmpdir)
            
            # Check migration worked
            assert mem.get_entry("entry1") is not None
            assert mem.get_entry("entry1")["content"] == "Content 1"
            
            assert mem.get_entry("entry2") is not None
            assert mem.get_entry("entry2")["content"] == "Content 2"
            assert mem.get_entry("entry2")["meta"]["source"] == "test.md"
    
    def test_v2_schema(self):
        """Should save in v2 format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            mem.upsert("test1", "Content", {"source": "test.md"}, mtime=123456.0)
            
            # Check saved format
            index_file = Path(tmpdir) / "index.json"
            with open(index_file) as f:
                data = json.load(f)
            
            assert data["version"] == 2
            assert "entries" in data
            assert "test1" in data["entries"]
            assert data["entries"]["test1"]["meta"]["source"] == "test.md"
            assert data["entries"]["test1"]["mtime"] == 123456.0
