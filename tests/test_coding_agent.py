"""Tests for Coding Agent Skill."""

import pytest
import tempfile
import subprocess
from pathlib import Path


class TestCodingAgent:
    """Tests for coding_agent function."""
    
    def test_help_command(self):
        """Test help command returns help output."""
        from skills.coding_agent.skill import coding_agent
        
        result = coding_agent(command="help")
        
        assert result.success is True
        assert "Coding Agent Commands" in result.output
    
    def test_unknown_command(self):
        """Test unknown command returns error."""
        from skills.coding_agent.skill import coding_agent
        
        result = coding_agent(command="unknown")
        
        assert result.success is False
        assert "Unknown command" in result.error
    
    def test_check_codex_available(self):
        """Test check command for available agent."""
        from skills.coding_agent.skill import coding_agent
        from skills.decorator import SkillResult
        
        result = coding_agent(command="check", agent="codex")
        
        # Should return success or failure with error message
        assert isinstance(result, SkillResult)
        assert result.success or (result.error is not None and ("not available" in result.error or "not installed" in result.error))
    
    def test_monitor_command(self):
        """Test monitor command returns monitoring guidance."""
        from skills.coding_agent.skill import coding_agent
        
        result = coding_agent(command="monitor")
        
        assert result.success is True
        assert "Process Tool Actions" in result.output


class TestAgentCommands:
    """Tests for agent command building."""
    
    def test_get_agent_command_codex(self):
        """Test Codex command building."""
        from skills.coding_agent.skill import _get_agent_command, _codex_command
        
        # Test full-auto mode
        cmd = _codex_command("test prompt", "full-auto")
        assert "codex" in cmd
        assert "--full-auto" in " ".join(cmd)
        
        # Test yolo mode
        cmd = _codex_command("test prompt", "yolo")
        assert "--yolo" in " ".join(cmd)
    
    def test_get_agent_command_claude(self):
        """Test Claude command building."""
        from skills.coding_agent.skill import _claude_command
        
        cmd = _claude_command("test prompt")
        assert "claude" in cmd
        assert "test prompt" in cmd
    
    def test_get_agent_command_pi(self):
        """Test Pi command building."""
        from skills.coding_agent.skill import _pi_command
        
        cmd = _pi_command("test prompt")
        assert "pi" in cmd
    
    def test_get_agent_command_opencode(self):
        """Test OpenCode command building."""
        from skills.coding_agent.skill import _opencode_command
        
        cmd = _opencode_command("test prompt")
        assert "opencode" in cmd


class TestGitWorktreeManager:
    """Tests for git_worktree_manager function."""
    
    def test_help_command(self):
        """Test help command returns help output."""
        from skills.coding_agent.skill import git_worktree_manager
        
        result = git_worktree_manager(command="help")
        
        assert result.success is True
        assert "Git Worktree Manager" in result.output
    
    def test_list_command(self):
        """Test list command."""
        from skills.coding_agent.skill import git_worktree_manager
        
        result = git_worktree_manager(command="list")
        
        # May or may not have worktrees
        assert result.success is True
        # Result should contain worktree info or "No worktrees"
    
    def test_unknown_command(self):
        """Test unknown command returns error."""
        from skills.coding_agent.skill import git_worktree_manager
        
        result = git_worktree_manager(command="unknown")
        
        assert result.success is False
        assert "Unknown command" in result.error
    
    def test_create_requires_params(self):
        """Test that create requires branch and path."""
        from skills.coding_agent.skill import git_worktree_manager
        
        # Missing branch
        result = git_worktree_manager(command="create", path="/tmp/test")
        assert result.success is False
        assert "required" in result.error
        
        # Missing path
        result = git_worktree_manager(command="create", branch="test")
        assert result.success is False
        assert "required" in result.error
    
    def test_remove_requires_path(self):
        """Test that remove requires path."""
        from skills.coding_agent.skill import git_worktree_manager
        
        result = git_worktree_manager(command="remove")
        assert result.success is False
        assert "path is required" in result.error


class TestAgentAvailability:
    """Tests for agent availability checking."""
    
    def test_check_codex_available(self):
        """Test checking Codex availability."""
        from skills.coding_agent.skill import _check_agent_available
        
        result = _check_agent_available("codex")
        # Boolean result
        assert isinstance(result, bool)
    
    def test_check_unknown_agent(self):
        """Test checking unknown agent."""
        from skills.coding_agent.skill import _check_agent_available
        
        result = _check_agent_available("nonexistent-agent-xyz")
        assert result is False
    
    def test_check_case_insensitive(self):
        """Test that agent check is case insensitive."""
        from skills.coding_agent.skill import _check_agent_available
        
        # Both should give same result
        result_lower = _check_agent_available("codex")
        result_upper = _check_agent_available("CODEX")
        
        assert result_lower == result_upper


class TestSkillResult:
    """Tests for SkillResult integration."""
    
    def test_skill_result_structure(self):
        """Test that skill returns proper SkillResult."""
        from skills.coding_agent.skill import coding_agent
        
        result = coding_agent(command="help")
        
        assert hasattr(result, "success")
        assert hasattr(result, "output")
        assert hasattr(result, "error")
        assert hasattr(result, "data")
    
    def test_success_result(self):
        """Test success result has output."""
        from skills.coding_agent.skill import coding_agent
        
        result = coding_agent(command="help")
        
        assert result.success is True
        assert result.output is not None
        assert result.error is None
    
    def test_error_result(self):
        """Test error result has error message."""
        from skills.coding_agent.skill import coding_agent
        
        result = coding_agent(command="check", agent="nonexistent-agent")
        
        assert result.success is False or "not available" in (result.output or "")


class TestDocumentation:
    """Tests for documentation completeness."""
    
    def test_skill_md_exists(self):
        """Test that SKILL.md exists."""
        import os
        # Navigate from tests/ to skills/coding_agent/
        skill_path = Path(__file__).resolve().parent.parent / "skills" / "coding_agent" / "SKILL.md"
        assert skill_path.exists(), f"SKILL.md not found at {skill_path}"
    
    def test_skill_md_has_frontmatter(self):
        """Test that SKILL.md has YAML frontmatter."""
        import os
        skill_path = Path(__file__).resolve().parent.parent / "skills" / "coding_agent" / "SKILL.md"
        content = skill_path.read_text()
        
        assert "---" in content
        assert "name:" in content
        assert "description:" in content
    
    def test_skill_md_has_examples(self):
        """Test that SKILL.md has examples."""
        import os
        skill_path = Path(__file__).resolve().parent.parent / "skills" / "coding_agent" / "SKILL.md"
        content = skill_path.read_text()
        
        assert "Example" in content or "example" in content
        assert "```bash" in content or "```python" in content
    
    def test_skill_md_documents_modes(self):
        """Test that SKILL.md documents execution modes."""
        import os
        skill_path = Path(__file__).resolve().parent.parent / "skills" / "coding_agent" / "SKILL.md"
        content = skill_path.read_text()
        
        # Should document --full-auto, --yolo, vanilla
        assert "--full-auto" in content or "full-auto" in content
        assert "--yolo" in content or "yolo" in content
    
    def test_skill_md_documents_pty(self):
        """Test that SKILL.md documents PTY requirement."""
        import os
        skill_path = Path(__file__).resolve().parent.parent / "skills" / "coding_agent" / "SKILL.md"
        content = skill_path.read_text()
        
        assert "pty" in content.lower()
