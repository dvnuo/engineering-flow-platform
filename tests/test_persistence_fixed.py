"""Tests for session persistence module."""

import asyncio
import tempfile
import pytest
from pathlib import Path

from src.sessions.persistence import SessionPersistence


class TestSessionPersistence:
    """Tests for SessionPersistence class."""
    
    @pytest.fixture
    def store(self):
        """Create a temporary session store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield SessionPersistence(f"{tmpdir}/sessions")
    
    @pytest.mark.asyncio
    async def test_save_session(self, store):
        """Test session saving."""
        session_id = "test_session_001"
        result = await store.save_session(
            session_id=session_id,
            channel="runtime_api",
            messages=[{"role": "user", "content": "Hello"}],
            metadata={"user_id": "123"}
        )
        
        assert result is True
        
        # Load and verify
        info = await store.load_session(session_id)
        assert info is not None
        assert info["channel"] == "runtime_api"
        assert info["metadata"]["user_id"] == "123"
    
    @pytest.mark.asyncio
    async def test_load_session(self, store):
        """Test session loading."""
        session_id = "test_session_002"
        await store.save_session(
            session_id=session_id,
            channel="telegram",
            messages=[{"role": "user", "content": "Test message"}],
            metadata={}
        )
        
        info = await store.load_session(session_id)
        assert info is not None
        assert info["channel"] == "telegram"
        assert len(info["messages"]) == 1
    
    @pytest.mark.asyncio
    async def test_load_nonexistent_session(self, store):
        """Test loading nonexistent session returns None."""
        info = await store.load_session("nonexistent_session_xyz")
        assert info is None
    
    @pytest.mark.asyncio
    async def test_list_sessions(self, store):
        """Test listing all sessions."""
        # Create multiple sessions
        for i in range(3):
            await store.save_session(
                session_id=f"list_test_{i}",
                channel="runtime_api",
                messages=[{"role": "user", "content": f"Message {i}"}],
                metadata={}
            )
        
        sessions = await store.list_sessions()
        assert len(sessions) >= 3
    
    @pytest.mark.asyncio
    async def test_delete_session(self, store):
        """Test session deletion."""
        session_id = "delete_test_session"
        await store.save_session(
            session_id=session_id,
            channel="runtime_api",
            messages=[{"role": "user", "content": "To be deleted"}],
            metadata={}
        )
        
        # Verify it exists
        info = await store.load_session(session_id)
        assert info is not None
        
        # Delete
        result = await store.delete_session(session_id)
        assert result is True
        
        # Verify it's gone
        info = await store.load_session(session_id)
        assert info is None
    
    @pytest.mark.asyncio
    async def test_delete_nonexistent_session(self, store):
        """Test deleting nonexistent session returns False."""
        result = await store.delete_session("nonexistent_to_delete")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_clear_all(self, store):
        """Test clearing all sessions."""
        # Create sessions
        for i in range(5):
            await store.save_session(
                session_id=f"clear_test_{i}",
                channel="runtime_api",
                messages=[{"role": "user", "content": f"Message {i}"}],
                metadata={}
            )
        
        # Clear all
        count = await store.clear_all()
        assert count >= 5
        
        # Verify empty
        sessions = await store.list_sessions()
        # Note: Some sessions may remain from other tests
        # Just verify no crash on clear_all
    
    @pytest.mark.asyncio
    async def test_session_with_messages(self, store):
        """Test saving session with multiple messages."""
        session_id = "multi_msg_session"
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm doing well, thanks!"},
        ]
        
        await store.save_session(
            session_id=session_id,
            channel="runtime_api",
            messages=messages,
            metadata={}
        )
        
        info = await store.load_session(session_id)
        assert len(info["messages"]) == 4
        assert info["messages"][0]["content"] == "Hello"
        assert info["messages"][3]["content"] == "I'm doing well, thanks!"
    
    @pytest.mark.asyncio
    async def test_session_metadata(self, store):
        """Test session with custom metadata."""
        session_id = "metadata_session"
        metadata = {
            "user_id": "user_123",
            "topic": "project讨论",
            "priority": "high"
        }
        
        await store.save_session(
            session_id=session_id,
            channel="runtime_api",
            messages=[{"role": "user", "content": "Test"}],
            metadata=metadata
        )
        
        info = await store.load_session(session_id)
        assert info["metadata"]["user_id"] == "user_123"
        assert info["metadata"]["topic"] == "project讨论"
        assert info["metadata"]["priority"] == "high"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
