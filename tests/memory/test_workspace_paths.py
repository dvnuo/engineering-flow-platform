"""Tests for workspace path consistency (Step E)."""

import tempfile
import pytest
from pathlib import Path
from datetime import datetime
from src.memory import (
    MemoryConfig,
    get_memory_dir,
    get_memory_path,
    get_long_term_memory_path,
    write_daily_memory,
    write_long_term_memory,
)
from src.workspace_defaults import DEFAULT_RUNTIME_WORKSPACE, resolve_runtime_workspace


class TestWorkspacePaths:
    """Tests for workspace-aware path functions."""

    @pytest.mark.parametrize(
        "config_data",
        [
            None,
            {},
            {"workspace": {"path": None}},
            {"workspace": {"path": ""}},
            {"workspace": ""},
        ],
    )
    def test_resolve_runtime_workspace_defaults_to_runtime_workspace(self, config_data):
        assert resolve_runtime_workspace(config_data) == DEFAULT_RUNTIME_WORKSPACE

    @pytest.mark.parametrize(
        "legacy_path",
        [
            "~/.efp/workspace",
            "~/.efp/workspace/",
            Path.home() / ".efp" / "workspace",
            "/root/.efp/workspace",
        ],
    )
    def test_resolve_runtime_workspace_treats_legacy_default_as_alias(self, legacy_path):
        assert (
            resolve_runtime_workspace({"workspace": {"path": legacy_path}})
            == DEFAULT_RUNTIME_WORKSPACE
        )

    def test_resolve_runtime_workspace_preserves_custom_override(self, tmp_path):
        custom_workspace = tmp_path / "custom-workspace"

        assert (
            resolve_runtime_workspace({"workspace": {"path": str(custom_workspace)}})
            == custom_workspace
        )

    def test_memory_config_uses_runtime_workspace_for_legacy_workspace_path(self, tmp_path):
        cfg = MemoryConfig(
            {
                "path": str(tmp_path / "memory-store"),
                "workspace": {"path": "/root/.efp/workspace"},
            }
        )

        assert cfg.workspace_dir == DEFAULT_RUNTIME_WORKSPACE

    def test_memory_config_preserves_custom_workspace_path(self, tmp_path):
        custom_workspace = tmp_path / "custom-workspace"
        cfg = MemoryConfig(
            {
                "path": str(tmp_path / "memory-store"),
                "workspace": {"path": str(custom_workspace)},
            }
        )

        assert cfg.workspace_dir == custom_workspace

    def test_get_memory_dir_default(self):
        """Should use the runtime workspace if not specified."""

        # Test that the function returns the runtime workspace memory directory
        # without actually creating it
        result = get_memory_dir()

        assert result == DEFAULT_RUNTIME_WORKSPACE / "memory"
    
    def test_get_memory_dir_custom_workspace(self):
        """Should use custom workspace when provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            result = get_memory_dir(workspace)
            
            assert result == workspace / "memory"
            assert result.exists()
    
    def test_get_memory_path_with_custom_workspace(self):
        """Should use custom workspace and date."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            result = get_memory_path(workspace, "2026-01-15")
            
            assert result == workspace / "memory" / "2026-01-15.md"
    
    def test_get_long_term_memory_path_with_custom_workspace(self):
        """Should use custom workspace for MEMORY.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            result = get_long_term_memory_path(workspace)
            
            assert result == workspace / "MEMORY.md"
    
    def test_get_long_term_memory_path_custom_workspace(self):
        """Should use custom workspace for MEMORY.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            result = get_long_term_memory_path(workspace)
            
            assert result == workspace / "MEMORY.md"
    
    def test_write_daily_memory(self):
        """Should write to correct workspace (append mode by default)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            filepath = write_daily_memory(
                workspace,
                "Test daily note content",
                "2026-03-03"
            )
            
            assert filepath == workspace / "memory" / "2026-03-03.md"
            assert filepath.exists()
            # Default append mode adds newline
            assert filepath.read_text().rstrip("\n") == "Test daily note content"
    
    def test_write_daily_memory_creates_dir(self):
        """Should create memory directory if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            write_daily_memory(workspace, "Content", "2026-03-03")
            
            assert (workspace / "memory").exists()
    
    def test_write_long_term_memory(self):
        """Should write MEMORY.md to correct workspace (append by default)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            
            filepath = write_long_term_memory(workspace, "Long-term memory content")
            
            assert filepath == workspace / "MEMORY.md"
            assert filepath.exists()
            # Default append mode adds newline
            assert filepath.read_text().rstrip("\n") == "Long-term memory content"
    
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
                
                # Verify isolation (strip trailing newline from daily notes and MEMORY.md)
                assert (ws1 / "memory" / "2026-03-03.md").read_text().rstrip("\n") == "Workspace 1 note"
                assert (ws2 / "memory" / "2026-03-03.md").read_text().rstrip("\n") == "Workspace 2 note"
                assert (ws1 / "MEMORY.md").read_text().rstrip("\n") == "Workspace 1 memory"
                assert (ws2 / "MEMORY.md").read_text().rstrip("\n") == "Workspace 2 memory"
