"""Tests for memory operators (Step F)."""

import tempfile
import pytest
from src.memory.operators import (
    MemoryOp,
    MemoryOpType,
    apply_ops,
    create_add_op,
    create_update_op,
    create_delete_op,
    create_merge_op,
    create_noop_op,
)
from src.memory.lightweight import LightweightMemory


class TestMemoryOp:
    """Tests for MemoryOp dataclass."""
    
    def test_create_add_op(self):
        """Should create ADD operation."""
        op = create_add_op("Test content", {"source": "test"}, "Testing")
        
        assert op.op == MemoryOpType.ADD
        assert op.payload['content'] == "Test content"
        assert op.payload['metadata']['source'] == "test"
        assert op.reason == "Testing"
    
    def test_create_update_op(self):
        """Should create UPDATE operation."""
        op = create_update_op("entry1", "New content")
        
        assert op.op == MemoryOpType.UPDATE
        assert op.target_id == "entry1"
        assert op.payload['content'] == "New content"
    
    def test_create_delete_op(self):
        """Should create DELETE operation."""
        op = create_delete_op("entry1", "Removing")
        
        assert op.op == MemoryOpType.DELETE
        assert op.target_id == "entry1"
        assert op.reason == "Removing"
    
    def test_create_merge_op(self):
        """Should create MERGE operation."""
        op = create_merge_op("entry1", "Additional content")
        
        assert op.op == MemoryOpType.MERGE
        assert op.target_id == "entry1"
        assert op.payload['content'] == "Additional content"
    
    def test_create_noop_op(self):
        """Should create NOOP operation."""
        op = create_noop_op("No changes needed")
        
        assert op.op == MemoryOpType.NOOP
        assert op.reason == "No changes needed"


class TestApplyOps:
    """Tests for apply_ops function."""
    
    def test_apply_add(self):
        """Should add new entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            
            ops = [create_add_op("Test content", {"source": "test.md"})]
            results = apply_ops(mem, ops)
            
            assert len(results) == 1
            assert "ADD" in results[0]
            # Check that some entry was added (the ID is generated from hash)
            assert mem.count() > 0
    
    def test_apply_update(self):
        """Should update existing entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            
            # Add first
            mem.upsert("entry1", "Original content")
            
            # Update
            ops = [create_update_op("entry1", "Updated content")]
            results = apply_ops(mem, ops)
            
            assert "UPDATE" in results[0]
            assert mem.get_entry("entry1")["content"] == "Updated content"
    
    def test_apply_delete(self):
        """Should delete entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            
            # Add first
            mem.upsert("entry1", "Content")
            
            # Delete
            ops = [create_delete_op("entry1")]
            results = apply_ops(mem, ops)
            
            assert "DELETE" in results[0]
            assert mem.get_entry("entry1") is None
    
    def test_apply_merge(self):
        """Should merge content to existing entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            
            # Add first
            mem.upsert("entry1", "Original content")
            
            # Merge
            ops = [create_merge_op("entry1", "Additional content")]
            results = apply_ops(mem, ops)
            
            assert "MERGE" in results[0]
            content = mem.get_entry("entry1")["content"]
            assert "Original content" in content
            assert "Additional content" in content
    
    def test_apply_noop(self):
        """Should do nothing for NOOP."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            
            ops = [create_noop_op("No changes needed")]
            results = apply_ops(mem, ops)
            
            assert "NOOP" in results[0]
            assert results[0] == "NOOP: No changes needed"
    
    def test_apply_multiple_ops(self):
        """Should apply multiple operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = LightweightMemory(storage_dir=tmpdir)
            
            ops = [
                create_add_op("Content 1", {"source": "a.md"}),
                create_add_op("Content 2", {"source": "b.md"}),
                create_delete_op("mem:", "Removed"),
            ]
            results = apply_ops(mem, ops)
            
            assert len(results) == 3
