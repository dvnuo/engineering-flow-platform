"""Tests for Sub-agent Sessions Tools."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


class TestSubAgent:
    """Tests for SubAgent class."""
    
    def test_subagent_initialization(self):
        """Test SubAgent initialization."""
        from src.agents.subagent import SubAgent
        
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
        from src.agents.subagent import SubAgent
        
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
        from src.agents.subagent import SubAgent
        
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
        from src.agents.subagent import sessions_list
        
        # Mock session_manager - patch where it's imported from, not where it's used
        with patch('session.manager.session_manager') as mock_sm:
            mock_sm.get_session_info.return_value = None
            
            result = sessions_list()
            data = json.loads(result)
            
            assert data["total"] == 0
            assert len(data["sessions"]) == 0
    
    def test_sessions_list_with_limit(self):
        """Test sessions_list with limit parameter."""
        from src.agents.subagent import sessions_list
        
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
        from src.agents.subagent import sessions_history
        
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
        from src.agents.subagent import sessions_spawn, _subagent_sessions
        
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
        from src.agents.subagent import sessions_spawn, _subagent_sessions
        
        _subagent_sessions.clear()
        
        result = sessions_spawn(task="Auto task")
        data = json.loads(result)
        
        assert data["status"] == "started"
        assert "subagent-" in data["session_key"]
        assert data["task_preview"] == "Auto task"
        
        # Cleanup
        _subagent_sessions.clear()

    def test_sessions_spawn_routes_through_execution_bus(self, monkeypatch):
        from src.agents import subagent

        class _FakeBus:
            def __init__(self):
                self.requests = []

            async def execute(self, request):
                self.requests.append(request)
                return type(
                    "R",
                    (),
                    {
                        "output_payload": {
                            "session_key": request.session_id,
                            "status": "started",
                            "task_preview": "Task",
                            "message": "Sub-agent session started",
                        }
                    },
                )()

        fake_bus = _FakeBus()
        monkeypatch.setattr("src.runtime.build_default_execution_bus", lambda *args, **kwargs: fake_bus)

        result = subagent.sessions_spawn(task="Task", label="bus-session")
        data = json.loads(result)

        assert data["status"] == "started"
        assert fake_bus.requests
        assert fake_bus.requests[0].execution_type == "subagent"

    def test_sessions_spawn_surfaces_non_loop_runtime_error(self, monkeypatch):
        from src.agents import subagent

        class _FakeBus:
            async def execute(self, request):
                raise RuntimeError("bus explode")

        monkeypatch.setattr("src.runtime.build_default_execution_bus", lambda *args, **kwargs: _FakeBus())

        with pytest.raises(RuntimeError, match="bus explode"):
            subagent.sessions_spawn(task="Task", label="error-session")

    def test_sessions_spawn_running_loop_uses_create_task(self, monkeypatch):
        from src.agents import subagent

        class _FakeBus:
            async def execute(self, request):
                return type("R", (), {"output_payload": {"status": "started", "session_key": request.session_id}})()

        class _Task:
            def __init__(self, coro):
                self.coro = coro
                self.callbacks = []

            def add_done_callback(self, cb):
                self.callbacks.append(cb)

            def close(self):
                self.coro.close()

        class _Loop:
            def __init__(self):
                self.tasks = []

            def is_running(self):
                return True

            def create_task(self, coro):
                task = _Task(coro)
                self.tasks.append(task)
                return task

        fake_loop = _Loop()
        monkeypatch.setattr("src.runtime.build_default_execution_bus", lambda *args, **kwargs: _FakeBus())
        monkeypatch.setattr("src.agents.subagent.asyncio.get_running_loop", lambda: fake_loop)

        result = subagent.sessions_spawn(task="Task", label="loop-session")
        data = json.loads(result)
        assert data["status"] == "started"
        assert fake_loop.tasks
        assert fake_loop.tasks[0].callbacks
        assert "loop-session" in subagent._subagent_sessions
        assert subagent._subagent_sessions["loop-session"]["parent_session_id"] is None
        fake_loop.tasks[0].close()

    def test_background_task_exception_callback_logs(self, monkeypatch):
        from src.agents import subagent

        class _Task:
            def cancelled(self):
                return False

            def exception(self):
                return RuntimeError("boom")

        log_calls = []
        monkeypatch.setattr(subagent.logger, "error", lambda *args, **kwargs: log_calls.append(args))
        subagent._log_background_task_exception(_Task(), "s1")
        assert log_calls


class TestSubAgentSchemas:
    """Tests for subagent_schemas module."""
    
    def test_subagent_tools_defined(self):
        """Test that SUBAGENT_TOOLS is defined."""
        from src.agents.subagent_schemas import SUBAGENT_TOOLS
        
        assert isinstance(SUBAGENT_TOOLS, list)
        assert len(SUBAGENT_TOOLS) > 0
    
    def test_sessions_list_schema(self):
        """Test sessions_list tool schema."""
        from src.agents.subagent_schemas import SUBAGENT_TOOLS
        
        schema = SUBAGENT_TOOLS[0]
        
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "sessions_list"
        assert "parameters" in schema["function"]
    
    def test_sessions_spawn_schema(self):
        """Test sessions_spawn tool schema."""
        from src.agents.subagent_schemas import SUBAGENT_TOOLS
        
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
        from src.agents.subagent import _subagent_sessions
        
        assert isinstance(_subagent_sessions, dict)

    def test_list_active_subagent_summaries_filters_parent_session(self):
        from src.agents.subagent import _subagent_sessions, list_active_subagent_summaries

        _subagent_sessions.clear()
        _subagent_sessions["sa-1"] = {
            "session_key": "sa-1",
            "task": "t1",
            "status": "started",
            "model": "gpt-4",
            "thinking": "low",
            "created_at": "2026-01-01T00:00:00Z",
            "parent_session_id": "s-a",
        }
        _subagent_sessions["sa-2"] = {
            "session_key": "sa-2",
            "task": "t2",
            "status": "started",
            "model": "gpt-4",
            "thinking": "low",
            "created_at": "2026-01-01T00:00:01Z",
            "parent_session_id": "s-b",
        }

        filtered = list_active_subagent_summaries(parent_session_id="s-a")
        assert len(filtered) == 1
        assert filtered[0]["session_key"] == "sa-1"
        assert filtered[0]["parent_session_id"] == "s-a"
        _subagent_sessions.clear()
    
    def test_spawn_and_cleanup(self):
        """Test spawning and cleaning up a session."""
        from src.agents.subagent import sessions_spawn, _subagent_sessions
        
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


class TestSubAgentDisableTools:
    """Tests for sub-agent tools disable functionality."""
    
    def test_subagent_disable_tools_initialization(self):
        """Test SubAgent initialization with disable_tools=True."""
        from src.agents.subagent import SubAgent
        
        subagent = SubAgent(
            session_key="test-think-only",
            task="Think about this problem deeply",
            thinking="high",
            disable_tools=True
        )
        
        assert subagent.session_key == "test-think-only"
        assert subagent.disable_tools is True
        assert subagent.thinking == "high"
    
    def test_subagent_tools_disabled_when_flag_set(self):
        """Test that tools are disabled when disable_tools=True."""
        from src.agents.subagent import SubAgent
        
        subagent = SubAgent(
            session_key="test-tools-disabled",
            task="Just think, don't execute",
            disable_tools=True
        )
        
        # Get the agent (creates it)
        agent = subagent.agent
        
        # Tools should be empty
        assert agent.tools == []
    
    def test_subagent_tools_enabled_by_default(self):
        """Test that tools are enabled by default when disable_tools=False."""
        from src.agents.subagent import SubAgent
        
        subagent = SubAgent(
            session_key="test-tools-enabled",
            task="Execute normally",
            disable_tools=False
        )
        
        # Get the agent (creates it)
        agent = subagent.agent
        
        # Tools should NOT be empty
        assert len(agent.tools) > 0
    
    def test_sessions_spawn_disable_tools(self):
        """Test sessions_spawn with disable_tools parameter."""
        from src.agents.subagent import sessions_spawn, _subagent_sessions
        
        _subagent_sessions.clear()
        
        result = sessions_spawn(
            task="Deep thinking task",
            thinking="high",
            disable_tools=True,
            label="think-only-task"
        )
        
        data = json.loads(result)
        
        assert data["status"] == "started"
        assert data["disable_tools"] is True
        assert data["thinking"] == "high"
        
        # Cleanup
        _subagent_sessions.clear()
    
    def test_sessions_spawn_schema_has_disable_tools(self):
        """Test that sessions_spawn schema includes disable_tools."""
        from src.agents.subagent_schemas import SUBAGENT_TOOLS
        
        # Find sessions_spawn schema
        spawn_schema = None
        for tool in SUBAGENT_TOOLS:
            if tool["function"]["name"] == "sessions_spawn":
                spawn_schema = tool
                break
        
        assert spawn_schema is not None
        assert "disable_tools" in spawn_schema["function"]["parameters"]["properties"]
        
        props = spawn_schema["function"]["parameters"]["properties"]["disable_tools"]
        assert props["type"] == "boolean"
        assert "pure thinking" in props["description"].lower()
    
    def test_subagent_to_dict_includes_disable_tools(self):
        """Test that to_dict includes disable_tools field."""
        from src.agents.subagent import SubAgent
        
        subagent = SubAgent(
            session_key="test-dict",
            task="Test task",
            disable_tools=True
        )
        
        result = subagent.to_dict()
        
        assert "disable_tools" in result
        assert result["disable_tools"] is True
