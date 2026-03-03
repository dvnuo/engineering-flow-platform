"""Tests for memory refresh functionality (Step C)."""

import json
import tempfile
import time
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from src.agents.memory import MemorySystem


class TestMemoryRefresh:
    """Tests for auto-refresh index functionality."""
    
    def test_refresh_detects_file_change(self):
        """Should detect when indexed file changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create MEMORY.md
            memory_file = workspace / "MEMORY.md"
            memory_file.write_text("Initial content")
            
            # Initialize memory system
            mem = MemorySystem(
                workspace_path=str(workspace),
                search_config={"storage_dir": str(workspace / "search")},
            )
            
            # Initial index should have the file
            results = mem.search("Initial")
            assert len(results) > 0
            
            # Modify the file
            time.sleep(0.1)  # Ensure mtime changes
            memory_file.write_text("Updated content")
            
            # Refresh should detect the change
            refreshed = mem.refresh_index_if_needed()
            
            assert refreshed is True
            
            # New content should be searchable
            results = mem.search("Updated")
            assert len(results) > 0
    
    def test_refresh_no_change(self):
        """Should not reindex if file hasn't changed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create MEMORY.md
            memory_file = workspace / "MEMORY.md"
            memory_file.write_text("Content")
            
            # Initialize memory system
            mem = MemorySystem(
                workspace_path=str(workspace),
                search_config={"storage_dir": str(workspace / "search")},
            )
            
            # Refresh should not detect changes
            refreshed = mem.refresh_index_if_needed()
            
            assert refreshed is False
    
    def test_refresh_daily_notes(self):
        """Should detect new daily notes added after initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            memory_dir = workspace / "memory"
            memory_dir.mkdir()
            
            # Create workspace with no daily notes initially
            (workspace / "MEMORY.md").write_text("Memory content")
            
            # Initialize memory system (will index MEMORY.md but not daily notes yet)
            mem = MemorySystem(
                workspace_path=str(workspace),
                search_config={"storage_dir": str(workspace / "search")},
                daily_notes_index_days=7,
            )
            
            # Create today's daily note AFTER initialization
            today = datetime.now().strftime("%Y-%m-%d")
            daily_file = memory_dir / f"{today}.md"
            daily_file.write_text("# Daily Notes\n\nSome unique keyword XYZ123")
            
            # Refresh should detect the new file
            refreshed = mem.refresh_index_if_needed()
            
            assert refreshed is True
            
            # Search should find the daily note
            results = mem.search("XYZ123")
            assert len(results) > 0
            # Check it's from daily notes
            assert any(r["meta"].get("kind") == "daily" for r in results)


class TestMemorySourceRegistry:
    """Tests for source registry and configuration."""
    
    def test_daily_notes_config(self):
        """Should respect daily_notes_index_days config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            mem = MemorySystem(
                workspace_path=str(workspace),
                search_config={"storage_dir": str(workspace / "search")},
                daily_notes_index_days=30,
            )
            
            assert mem.daily_notes_index_days == 30
    
    def test_core_files_indexed(self):
        """Should index core memory files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            # Create core files
            (workspace / "MEMORY.md").write_text("Memory content")
            (workspace / "USER.md").write_text("User content")
            
            mem = MemorySystem(
                workspace_path=str(workspace),
                search_config={"storage_dir": str(workspace / "search")},
            )
            
            # Should be able to search both
            results_mem = mem.search("Memory")
            results_user = mem.search("User")
            
            assert len(results_mem) > 0
            assert len(results_user) > 0
