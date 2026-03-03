"""Tests for workspace path consistency (Step E)."""

import tempfile
import pytest
from pathlib import Path
from datetime import datetime
from src.memory import (
    get_memory_dir,
    get_memory_path,
    get_long_term_memory_path,
    write_daily_memory,
    write_long_term_memory,
)


class TestWorkspacePaths:
    """Tests for workspace-aware path functions."""
    
    def test_get_memory_dir_default(self):
        """Should use DEFAULT_WORKSPACE if not specified."""
        from src.memory import DEFAULT_WORKSPACE
        
        result = get_memory_dir()
        
        assert result == DEFAULT_WORKSPACE / "memory"
    
    def test_get_memory_dir_custom_workspace(self):
        """Should use custom workspace when provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            result = get_memory_dir(workspace)
            
            assert result == workspace / "memory"
            assert result.exists()
    
    def test_get_memory_path_default(self):
        """Should use DEFAULT_WORKSPACE and today's date."""
        from src.memory import DEFAULT_WORKSPACE
        
        result = get_memory_path()
        
        expected = DEFAULT_WORKSPACE / "memory" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        assert result == expected
    
    def test_get_memory_path_custom_workspace_and_date(self):
        """Should use custom workspace and date."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            result = get_memory_path(workspace, "2026-01-15")
            
            assert result == workspace / "memory" / "2026-01-15.md"
    
    def test_get_long_term_memory_path_default(self):
        """Should use DEFAULT_WORKSPACE for MEMORY.md."""
        from src.memory import DEFAULT_WORKSPACE
        
        result = get_long_term_memory_path()
        
        assert result == DEFAULT_WORKSPACE / "MEMORY.md"
    
    def test_get_long_term_memory_path_custom_workspace(self):
        """Should use custom workspace for MEMORY.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            result = get_long_term_memory_path(workspace)
            
            assert result == workspace / "MEMORY.md"
    
    def test_write_daily_memory(self):
        """Should write to correct workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            filepath = write_daily_memory(
                workspace,
                "Test daily note content",
                "2026-03-03"
            )
            
            assert filepath == workspace / "memory" / "2026-03-03.md"
            assert filepath.exists()
            assert filepath.read_text() == "Test daily note content"
    
    def test_write_daily_memory_creates_dir(self):
        """Should create memory directory if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            write_daily_memory(workspace, "Content", "2026-03-03")
            
            assert (workspace / "memory").exists()
    
    def test_write_long_term_memory(self):
        """Should write MEMORY.md to correct workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            filepath = write_long_term_memory(workspace, "Long-term memory content")
            
            assert filepath == workspace / "MEMORY.md"
            assert filepath.exists()
            assert filepath.read_text() == "Long-term memory content"
    
    def test_write_to_different_workspaces(self):
        """Two workspaces should not conflict."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                ws1 = Path(tmpdir1)
                ws2 = Path(tmpdir2)
                
                # Write to workspace 1
                write_daily_memory(ws1, "Workspace 1 note", "2026-03-03")
                write_long_term_memory(ws1, "Workspace 1 memory")
                
                # Write to workspace 2
                write_daily_memory(ws2, "Workspace 2 note", "2026-03-03")
                write_long_term_memory(ws2, "Workspace 2 memory")
                
                # Verify isolation
                assert (ws1 / "memory" / "2026-03-03.md").read_text() == "Workspace 1 note"
                assert (ws2 / "memory" / "2026-03-03.md").read_text() == "Workspace 2 note"
                assert (ws1 / "MEMORY.md").read_text() == "Workspace 1 memory"
                assert (ws2 / "MEMORY.md").read_text() == "Workspace 2 memory"
