"""Tests for SessionManager."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from openclaw_mini.session.manager import SessionManager, session_manager


@pytest.fixture
def fresh_session():
    """Create a fresh session manager for isolation."""
    manager = SessionManager()
    yield manager
    # Cleanup is not needed as each test uses unique session IDs


@pytest.fixture
def temp_session_id():
    """Generate a unique session ID for each test."""
    import uuid
    return f"test_session_{uuid.uuid4().hex[:8]}"


class TestSessionManagerBasic:
    """Basic session management tests."""

    def test_add_message(self, temp_session_id):
        """Test adding messages to session."""
        session_manager.clear_history(temp_session_id)
        session_manager.add_message(temp_session_id, "user", "Hello")
        history = session_manager.get_history(temp_session_id)
        assert len(history) == 1
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"

    def test_get_history(self, temp_session_id):
        """Test getting conversation history."""
        session_manager.clear_history(temp_session_id)
        session_manager.add_message(temp_session_id, "user", "Hello")
        session_manager.add_message(temp_session_id, "assistant", "Hi there!")
        history = session_manager.get_history(temp_session_id)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    def test_clear_history(self, temp_session_id):
        """Test clearing session history."""
        session_manager.add_message(temp_session_id, "user", "Hello")
        session_manager.clear_history(temp_session_id)
        history = session_manager.get_history(temp_session_id)
        assert len(history) == 0

    def test_list_sessions(self, temp_session_id):
        """Test listing active sessions."""
        session_manager.clear_history(temp_session_id)
        session_manager.add_message(temp_session_id, "user", "test")
        sessions = session_manager.list_sessions()
        assert temp_session_id in sessions


class TestSessionManagerIsolation:
    """Session isolation tests."""

    def test_session_isolation(self):
        """Test that different sessions have isolated histories."""
        import uuid
        session_a = f"isolation_a_{uuid.uuid4().hex[:8]}"
        session_b = f"isolation_b_{uuid.uuid4().hex[:8]}"
        
        session_manager.clear_history(session_a)
        session_manager.clear_history(session_b)
        session_manager.add_message(session_a, "user", "secret A")
        session_manager.add_message(session_b, "user", "secret B")
        
        history_a = session_manager.get_history(session_a)
        history_b = session_manager.get_history(session_b)
        
        assert history_a[0]["content"] == "secret A"
        assert history_b[0]["content"] == "secret B"
        assert len(history_a) == 1
        assert len(history_b) == 1

    def test_session_with_prefix(self):
        """Test session with Discord prefix."""
        import uuid
        session_id = f"discord:123456:{uuid.uuid4().hex[:8]}"
        
        session_manager.clear_history(session_id)
        session_manager.add_message(session_id, "user", "test message")
        
        history = session_manager.get_history(session_id)
        assert len(history) == 1
        assert history[0]["content"] == "test message"


class TestSessionManagerHistory:
    """History management tests."""

    def test_history_limit(self):
        """Test that history respects max_history limit."""
        import uuid
        session_id = f"limit_test_{uuid.uuid4().hex[:8]}"
        manager = SessionManager(max_history=3)
        
        # Add 10 messages (5 user + 5 assistant)
        for i in range(10):
            manager.add_message(session_id, "user", f"user_{i}")
            manager.add_message(session_id, "assistant", f"assistant_{i}")
        
        history = manager.get_history(session_id)
        # Should be limited to 6 messages (3 pairs)
        assert len(history) <= 6

    def test_history_timestamps(self):
        """Test that messages have timestamps."""
        import uuid
        session_id = f"timestamp_test_{uuid.uuid4().hex[:8]}"
        
        session_manager.add_message(session_id, "user", "hello")
        history = session_manager.get_history(session_id)
        
        assert len(history) == 1
        assert "timestamp" in history[0]
        assert history[0]["timestamp"] is not None


class TestSessionManagerInfo:
    """Session info tests."""

    def test_get_session_info(self):
        """Test getting session info."""
        import uuid
        session_id = f"info_test_{uuid.uuid4().hex[:8]}"
        
        session_manager.clear_history(session_id)
        session_manager.add_message(session_id, "user", "test")
        info = session_manager.get_session_info(session_id)
        
        assert info is not None
        assert "history_count" in info
        assert info["history_count"] == 1
        assert "created_at" in info
        assert "updated_at" in info

    def test_get_nonexistent_session_info(self):
        """Test getting info for nonexistent session."""
        info = session_manager.get_session_info("nonexistent_session_xyz")
        assert info is None


class TestSessionManagerEdgeCases:
    """Edge case tests."""

    def test_empty_content(self):
        """Test adding empty message content."""
        import uuid
        session_id = f"empty_test_{uuid.uuid4().hex[:8]}"
        
        session_manager.clear_history(session_id)
        session_manager.add_message(session_id, "user", "")
        history = session_manager.get_history(session_id)
        
        assert len(history) == 1
        assert history[0]["content"] == ""

    def test_special_characters_content(self):
        """Test adding messages with special characters."""
        import uuid
        session_id = f"special_test_{uuid.uuid4().hex[:8]}"
        
        session_manager.clear_history(session_id)
        special_content = "Hello! 🌍 你好\n\tSpecial: \"quotes\" 'single' \\backslash"
        session_manager.add_message(session_id, "user", special_content)
        history = session_manager.get_history(session_id)
        
        assert history[0]["content"] == special_content

    def test_long_content(self):
        """Test adding long message content."""
        import uuid
        session_id = f"long_test_{uuid.uuid4().hex[:8]}"
        
        session_manager.clear_history(session_id)
        long_content = "x" * 10000  # 10KB message
        session_manager.add_message(session_id, "user", long_content)
        history = session_manager.get_history(session_id)
        
        assert history[0]["content"] == long_content
        assert len(history[0]["content"]) == 10000

    def test_multiple_roles(self):
        """Test adding messages with different roles."""
        import uuid
        session_id = f"roles_test_{uuid.uuid4().hex[:8]}"
        
        session_manager.clear_history(session_id)
        session_manager.add_message(session_id, "system", "You are a helpful assistant")
        session_manager.add_message(session_id, "user", "Hello")
        session_manager.add_message(session_id, "assistant", "Hi there!")
        session_manager.add_message(session_id, "user", "How are you?")
        
        history = session_manager.get_history(session_id)
        assert len(history) == 4
        assert history[0]["role"] == "system"
        assert history[1]["role"] == "user"
        assert history[2]["role"] == "assistant"
        assert history[3]["role"] == "user"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
