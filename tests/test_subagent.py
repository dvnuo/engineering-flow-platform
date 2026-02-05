"""Tests for Sub-agent Sessions Tools."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


class TestSubAgent:
    """Tests for SubAgent class."""
    
    def test_subagent_initialization(self):
        """Test SubAgent initialization."""
        from tools.subagent import SubAgent
        
        subagent = SubAgent(
            session_key="test-session",
            task="Test task",
            model="gpt-4",
            thinking="medium"
        )
        
        assert subagent.session_key == "test-session"
        assert subagent.task == "Test task"
        assert subagent.model == "gpt-4"
        assert subagent.thinking == "medium"
        assert subagent.status == "running"
        assert subagent.result is None
        assert subagent._agent is None
        assert subagent.created_at is not None
    
    def test_subagent_to_dict(self):
        """Test SubAgent to_dict method."""
        from tools.subagent import SubAgent
        
        subagent = SubAgent(
            session_key="test-session",
            task="A" * 200,  # Long task to test truncation
            model="gpt-4",
            thinking="high"
        )
        
        result = subagent.to_dict()
        
        assert result["session_key"] == "test-session"
        assert "..." in result["task_preview"]  # Should be truncated
        assert result["model"] == "gpt-4"
        assert result["thinking"] == "high"
        assert result["status"] == "running"
        assert "created_at" in result
    
    def test_subagent_short_task(self):
        """Test SubAgent with short task (no truncation)."""
        from tools.subagent import SubAgent
        
        subagent = SubAgent(
            session_key="short-session",
            task="Short task",
            model=None,
            thinking=None
        )
        
        result = subagent.to_dict()
        
        assert result["task_preview"] == "Short task"
        assert "..." not in result["task_preview"]


class TestSessionsList:
    """Tests for sessions_list function."""
    
    def test_sessions_list_empty(self):
        """Test sessions_list with no sessions."""
        from tools.subagent import sessions_list
        
        # Mock session_manager - patch where it's imported from, not where it's used
        with patch('session.manager.session_manager') as mock_sm:
            mock_sm.get_session_info.return_value = None
            
            result = sessions_list()
            data = json.loads(result)
            
            assert data["total"] == 0
            assert len(data["sessions"]) == 0
    
    def test_sessions_list_with_limit(self):
        """Test sessions_list with limit parameter."""
        from tools.subagent import sessions_list
        
        with patch('session.manager.session_manager') as mock_sm:
            mock_sm.get_session_info.return_value = {
                "updated_at": datetime.now().isoformat()
            }
            
            result = sessions_list(limit=5)
            data = json.loads(result)
            
            assert data["total"] >= 0


class TestSessionsHistory:
    """Tests for sessions_history function."""
    
    def test_sessions_history_empty(self):
        """Test sessions_history with non-existent session."""
        from tools.subagent import sessions_history
        
        with patch('session.manager.session_manager') as mock_sm:
            mock_sm.get_history.return_value = []
            
            result = sessions_history("non-existent")
            data = json.loads(result)
            
            assert data["session_key"] == "non-existent"
            assert data["count"] == 0
            assert data["messages"] == []


class TestSessionsSpawn:
    """Tests for sessions_spawn function."""
    
    def test_sessions_spawn_basic(self):
        """Test basic session spawning."""
        from tools.subagent import sessions_spawn, _subagent_sessions
        
        # Clear any existing sessions
        _subagent_sessions.clear()
        
        result = sessions_spawn(
            task="Test task",
            model="gpt-4",
            thinking="medium",
            cleanup="delete",
            label="test-session"
        )
        
        data = json.loads(result)
        
        assert data["status"] == "started"
        assert "test-session" in data["session_key"]
        assert data["model"] == "gpt-4"
        assert data["thinking"] == "medium"
        assert data["cleanup"] == "delete"
        
        # Cleanup
        _subagent_sessions.clear()
    
    def test_sessions_spawn_auto_label(self):
        """Test session spawning with auto-generated label."""
        from tools.subagent import sessions_spawn, _subagent_sessions
        
        _subagent_sessions.clear()
        
        result = sessions_spawn(task="Auto task")
        data = json.loads(result)
        
        assert data["status"] == "started"
        assert "subagent-" in data["session_key"]
        assert data["task_preview"] == "Auto task"
        
        # Cleanup
        _subagent_sessions.clear()


class TestSubAgentSchemas:
    """Tests for subagent_schemas module."""
    
    def test_subagent_tools_defined(self):
        """Test that SUBAGENT_TOOLS is defined."""
        from tools.subagent_schemas import SUBAGENT_TOOLS
        
        assert isinstance(SUBAGENT_TOOLS, list)
        assert len(SUBAGENT_TOOLS) > 0
    
    def test_sessions_list_schema(self):
        """Test sessions_list tool schema."""
        from tools.subagent_schemas import SUBAGENT_TOOLS
        
        schema = SUBAGENT_TOOLS[0]
        
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "sessions_list"
        assert "parameters" in schema["function"]
    
    def test_sessions_spawn_schema(self):
        """Test sessions_spawn tool schema."""
        from tools.subagent_schemas import SUBAGENT_TOOLS
        
        # Find sessions_spawn schema
        spawn_schema = None
        for tool in SUBAGENT_TOOLS:
            if tool["function"]["name"] == "sessions_spawn":
                spawn_schema = tool
                break
        
        assert spawn_schema is not None
        assert "task" in spawn_schema["function"]["parameters"]["required"]


class TestSubAgentIntegration:
    """Integration tests for sub-agent sessions."""
    
    def test_subagent_sessions_registry(self):
        """Test that _subagent_sessions is properly initialized."""
        from tools.subagent import _subagent_sessions
        
        assert isinstance(_subagent_sessions, dict)
    
    def test_spawn_and_cleanup(self):
        """Test spawning and cleaning up a session."""
        from tools.subagent import sessions_spawn, _subagent_sessions
        
        # Spawn
        _subagent_sessions.clear()
        result = sessions_spawn(task="Cleanup test", label="cleanup-test")
        data = json.loads(result)
        
        assert data["status"] == "started"
        session_key = data["session_key"]
        
        # Verify session exists
        assert session_key in _subagent_sessions
        
        # Cleanup
        _subagent_sessions.clear()
        
        # Verify cleaned up
        assert len(_subagent_sessions) == 0
