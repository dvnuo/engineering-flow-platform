"""Tests for memory system module."""

import os
import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timedelta

try:
    from agent.memory import MemorySystem
except ImportError:
    pytest.skip("Memory module not available", allow_module_level=True)


class TestMemorySystem:
    """Tests for MemorySystem class."""
    
    @pytest.fixture
    def memory_with_files(self):
        """Create a memory system with test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            
            # Create memory directory
            memory_dir = tmppath / "memory"
            memory_dir.mkdir()
            
            # Create test files
            (tmppath / "SOUL.md").write_text("You are a helpful assistant.")
            (tmppath / "USER.md").write_text("User name: Test User")
            (tmppath / "AGENTS.md").write_text("Workspace conventions here.")
            (tmppath / "TOOLS.md").write_text("Tool config here.")
            (tmppath / "MEMORY.md").write_text("Long-term memory content.")
            
            # Create daily notes
            today = datetime.now()
            (memory_dir / f"{today.strftime('%Y-%m-%d')}.md").write_text("Today's notes.")
            yesterday = today - timedelta(days=1)
            (memory_dir / f"{yesterday.strftime('%Y-%m-%d')}.md").write_text("Yesterday's notes.")
            
            yield MemorySystem(tmpdir)
    
    def test_load_soul(self, memory_with_files):
        """Test loading SOUL.md."""
        soul = memory_with_files.load_soul()
        assert soul == "You are a helpful assistant."
    
    def test_load_user(self, memory_with_files):
        """Test loading USER.md."""
        user = memory_with_files.load_user()
        assert user == "User name: Test User"
    
    def test_load_agents(self, memory_with_files):
        """Test loading AGENTS.md."""
        agents = memory_with_files.load_agents()
        assert agents == "Workspace conventions here."
    
    def test_load_tools_config(self, memory_with_files):
        """Test loading TOOLS.md."""
        tools = memory_with_files.load_tools_config()
        assert tools == "Tool config here."
    
    def test_load_memory(self, memory_with_files):
        """Test loading MEMORY.md."""
        memory = memory_with_files.load_memory()
        assert memory == "Long-term memory content."
    
    def test_load_daily_notes(self, memory_with_files):
        """Test loading daily notes."""
        notes = memory_with_files.load_daily_notes(days=2)
        assert "Today's notes" in notes or "Yesterday's notes" in notes
    
    def test_missing_file_returns_empty(self, memory_with_files):
        """Test that missing files return empty string."""
        # File that doesn't exist
        result = memory_with_files._load_file("DOES_NOT_EXIST.md")
        assert result is None
    
    def test_build_system_prompt(self, memory_with_files):
        """Test building complete system prompt."""
        prompt = memory_with_files.build_system_prompt(include_memory=True)
        
        assert "SOUL" in prompt or "You are a helpful assistant" in prompt
        assert "USER" in prompt or "Test User" in prompt
        assert "TOOLS" in prompt or "Tool config" in prompt
        assert "MEMORY" in prompt or "Long-term memory" in prompt
    
    def test_build_system_prompt_excludes_memory(self, memory_with_files):
        """Test that memory is excluded when include_memory=False."""
        prompt = memory_with_files.build_system_prompt(include_memory=False)
        
        # Should still have SOUL, USER, AGENTS, TOOLS
        assert "You are a helpful assistant" in prompt or "SOUL" in prompt
        assert "Test User" in prompt or "USER" in prompt
        
        # Memory content should not be included
        assert "Long-term memory content" not in prompt
    
    def test_caching(self, memory_with_files):
        """Test that files are cached."""
        # First load
        soul1 = memory_with_files.load_soul()
        
        # Modify file
        (memory_with_files.workspace / "SOUL.md").write_text("Modified content")
        
        # Should still return cached value
        soul2 = memory_with_files.load_soul()
        assert soul1 == soul2
        
        # Clear cache
        memory_with_files.clear_cache()
        
        # Now should return new value
        soul3 = memory_with_files.load_soul()
        assert soul3 == "Modified content"
    
    def test_empty_workspace(self):
        """Test with empty workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemorySystem(tmpdir)
            
            # All should return empty
            assert memory.load_soul() == ""
            assert memory.load_user() == ""
            assert memory.load_agents() == ""
            assert memory.load_memory() == ""
            assert memory.load_daily_notes() == ""


class TestMemorySystemIntegration:
    """Integration tests for memory system."""
    
    def test_default_workspace(self):
        """Test that default workspace is ~/.efp/workspace."""
        memory = MemorySystem()
        expected = Path.home() / ".efp" / "workspace"
        assert memory.workspace == expected
    
    def test_workspace_not_exists(self):
        """Test handling of non-existent workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create workspace that doesn't exist on disk
            workspace = Path(tmpdir) / "nonexistent"
            memory = MemorySystem(str(workspace))
            
            # Should return empty strings
            assert memory.load_soul() == ""
            assert memory.load_user() == ""
    
    def test_memory_with_special_characters(self):
        """Test loading files with special characters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "SOUL.md").write_text("""
# Special Characters Test

- **Bold** and *italic*
- Code: `print("hello")`
- List:
  1. First
  2. Second
- > Blockquote

中文测试
""")
            
            memory = MemorySystem(tmpdir)
            soul = memory.load_soul()
            
            assert "Special Characters Test" in soul
            assert "中文测试" in soul


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
