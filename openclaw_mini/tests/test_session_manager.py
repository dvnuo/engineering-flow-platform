"""Tests for SessionManager."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from openclaw_mini.session.manager import SessionManager, session_manager


def test_add_message():
    """Test adding messages to session."""
    session_manager.clear_history("test_session")
    session_manager.add_message("test_session", "user", "Hello")
    history = session_manager.get_history("test_session")
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Hello"


def test_get_history():
    """Test getting conversation history."""
    session_manager.clear_history("test_session")
    session_manager.add_message("test_session", "user", "Hello")
    session_manager.add_message("test_session", "assistant", "Hi there!")
    history = session_manager.get_history("test_session")
    assert len(history) == 2


def test_clear_history():
    """Test clearing session history."""
    session_manager.add_message("test_session", "user", "Hello")
    session_manager.clear_history("test_session")
    history = session_manager.get_history("test_session")
    assert len(history) == 0


def test_list_sessions():
    """Test listing active sessions."""
    session_manager.clear_history("list_test_1")
    session_manager.clear_history("list_test_2")
    session_manager.add_message("list_test_1", "user", "test")
    session_manager.add_message("list_test_2", "user", "test")
    sessions = session_manager.list_sessions()
    assert "list_test_1" in sessions
    assert "list_test_2" in sessions


def test_session_isolation():
    """Test that different sessions have isolated histories."""
    session_manager.clear_history("session_a")
    session_manager.clear_history("session_b")
    session_manager.add_message("session_a", "user", "secret A")
    session_manager.add_message("session_b", "user", "secret B")
    history_a = session_manager.get_history("session_a")
    history_b = session_manager.get_history("session_b")
    assert history_a[0]["content"] == "secret A"
    assert history_b[0]["content"] == "secret B"


if __name__ == "__main__":
    test_add_message()
    test_get_history()
    test_clear_history()
    test_list_sessions()
    test_session_isolation()
    print("All session tests passed!")
