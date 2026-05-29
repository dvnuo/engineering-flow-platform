"""Tests for SessionManager."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from src.sessions.manager import SessionManager


@pytest.fixture
def fresh_session_manager():
    """Create a fresh session manager for isolation."""
    manager = SessionManager()
    yield manager


@pytest.fixture
def temp_session_id():
    """Generate a unique session ID for each test."""
    import uuid
    return f"test_session_{uuid.uuid4().hex[:8]}"


class TestSessionManagerBasic:
    """Basic session management tests."""

    @pytest.mark.asyncio
    async def test_add_message(self, fresh_session_manager, temp_session_id):
        """Test adding messages to session."""
        await fresh_session_manager.clear_history(temp_session_id)
        await fresh_session_manager.add_message(temp_session_id, "user", "Hello")
        history = await fresh_session_manager.get_history(temp_session_id)
        assert len(history) == 1
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_get_history(self, fresh_session_manager, temp_session_id):
        """Test getting conversation history."""
        await fresh_session_manager.clear_history(temp_session_id)
        await fresh_session_manager.add_message(temp_session_id, "user", "Hello")
        await fresh_session_manager.add_message(temp_session_id, "assistant", "Hi there!")
        history = await fresh_session_manager.get_history(temp_session_id)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_clear_history(self, fresh_session_manager, temp_session_id):
        """Test clearing session history."""
        await fresh_session_manager.add_message(temp_session_id, "user", "Hello")
        await fresh_session_manager.clear_history(temp_session_id)
        history = await fresh_session_manager.get_history(temp_session_id)
        assert len(history) == 0

    @pytest.mark.asyncio
    async def test_list_sessions(self, fresh_session_manager, temp_session_id):
        """Test listing active sessions."""
        await fresh_session_manager.clear_history(temp_session_id)
        await fresh_session_manager.add_message(temp_session_id, "user", "test")
        sessions = await fresh_session_manager.list_sessions()
        assert temp_session_id in sessions


class TestSessionManagerIsolation:
    """Session isolation tests."""

    @pytest.mark.asyncio
    async def test_session_isolation(self, fresh_session_manager):
        """Test that different sessions have isolated histories."""
        import uuid
        session_a = f"isolation_a_{uuid.uuid4().hex[:8]}"
        session_b = f"isolation_b_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_a)
        await fresh_session_manager.clear_history(session_b)
        await fresh_session_manager.add_message(session_a, "user", "secret A")
        await fresh_session_manager.add_message(session_b, "user", "secret B")
        
        history_a = await fresh_session_manager.get_history(session_a)
        history_b = await fresh_session_manager.get_history(session_b)
        
        assert history_a[0]["content"] == "secret A"
        assert history_b[0]["content"] == "secret B"

    @pytest.mark.asyncio
    async def test_session_with_prefix(self, fresh_session_manager):
        """Test sessions with ID prefixes."""
        import uuid
        prefix = f"test_{uuid.uuid4().hex[:8]}"
        session_id = f"{prefix}_session"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "test message")
        
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1


class TestSessionManagerHistory:
    """History management tests."""

    @pytest.mark.asyncio
    async def test_history_limit(self, fresh_session_manager):
        """Test history size limit."""
        import uuid
        session_id = f"limit_test_{uuid.uuid4().hex[:8]}"
        fresh_session_manager.max_history = 5
        
        await fresh_session_manager.clear_history(session_id)
        for i in range(10):
            await fresh_session_manager.add_message(session_id, "user", f"user_{i}")
            await fresh_session_manager.add_message(session_id, "assistant", f"assistant_{i}")
        
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) <= 10

    @pytest.mark.asyncio
    async def test_history_timestamps(self, fresh_session_manager):
        """Test that history includes timestamps."""
        import uuid
        session_id = f"ts_test_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "hello")
        history = await fresh_session_manager.get_history(session_id)
        
        assert len(history) == 1
        assert "timestamp" in history[0]


class TestSessionManagerInfo:
    """Session info tests."""

    @pytest.mark.asyncio
    async def test_get_session_info(self, fresh_session_manager):
        """Test getting session information."""
        import uuid
        session_id = f"info_test_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "test")
        info = await fresh_session_manager.get_session_info(session_id)
        
        assert "history_count" in info
        assert info["history_count"] == 1

    @pytest.mark.asyncio
    async def test_get_nonexistent_session_info(self, fresh_session_manager):
        """Test getting info for nonexistent session."""
        import uuid
        session_id = f"nonexistent_{uuid.uuid4().hex[:8]}"
        
        info = await fresh_session_manager.get_session_info(session_id)
        assert info is None


class TestSessionManagerEdgeCases:
    """Edge case tests."""

    @pytest.mark.asyncio
    async def test_empty_content(self, fresh_session_manager):
        """Test handling empty content."""
        import uuid
        session_id = f"empty_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "")
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_special_characters_content(self, fresh_session_manager):
        """Test handling special characters."""
        import uuid
        session_id = f"special_{uuid.uuid4().hex[:8]}"
        special_content = "Hello! Global 中文 Celebration @#$%"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", special_content)
        history = await fresh_session_manager.get_history(session_id)
        
        assert history[0]["content"] == special_content

    @pytest.mark.asyncio
    async def test_long_content(self, fresh_session_manager):
        """Test handling long content."""
        import uuid
        session_id = f"long_{uuid.uuid4().hex[:8]}"
        long_content = ("word " * 1000).strip()
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", long_content)
        history = await fresh_session_manager.get_history(session_id)
        
        assert history[0]["content"] == long_content

    @pytest.mark.asyncio
    async def test_multiple_roles(self, fresh_session_manager):
        """Test messages with multiple roles."""
        import uuid
        session_id = f"roles_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "Hello")
        await fresh_session_manager.add_message(session_id, "assistant", "Hi there!")
        await fresh_session_manager.add_message(session_id, "user", "How are you?")
        await fresh_session_manager.add_message(session_id, "assistant", "I am doing well, thanks!")
        
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 4
