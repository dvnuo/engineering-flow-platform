"""Tests for session persistence module."""

import asyncio
import tempfile
import pytest
from pathlib import Path

from session.persistence import SessionStore


class TestSessionStore:
    """Tests for SessionStore class."""
    
    @pytest.fixture
    def store(self):
        """Create a temporary session store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield SessionStore(f"{tmpdir}/sessions")
    
    @pytest.mark.asyncio
    async def test_create_session(self, store):
        """Test session creation."""
        await store.ensure_dir()
        session_id = await store.create_session(
            "main",
            channel="discord",
            metadata={"user_id": "123"}
        )
        
        assert session_id is not None
        assert session_id.startswith("discord_")
        
        # Check store entry
        info = await store.get_session_info("main")
        assert info is not None
        assert info["channel"] == "discord"
        assert info["metadata"]["user_id"] == "123"
    
    @pytest.mark.asyncio
    async def test_append_message(self, store):
        """Test appending messages to transcript."""
        await store.ensure_dir()
        await store.create_session("test_session")
        
        result = await store.append_message(
            "test_session",
            role="user",
            content="Hello, world!"
        )
        
        assert result is True
        
        # Check transcript
        transcript = await store.get_transcript("test_session")
        assert len(transcript) == 1
        assert transcript[0]["role"] == "user"
        assert transcript[0]["content"] == "Hello, world!"
    
    @pytest.mark.asyncio
    async def test_get_transcript_limit(self, store):
        """Test transcript retrieval with limit."""
        await store.ensure_dir()
        await store.create_session("limited_session")
        
        # Add 10 messages
        for i in range(10):
            await store.append_message(
                "limited_session",
                role="user",
                content=f"Message {i}"
            )
        
        # Get last 5
        transcript = await store.get_transcript("limited_session", limit=5)
        assert len(transcript) == 5
        assert transcript[0]["content"] == "Message 5"
        assert transcript[-1]["content"] == "Message 9"
    
    @pytest.mark.asyncio
    async def test_list_sessions(self, store):
        """Test listing all sessions."""
        await store.ensure_dir()
        await store.create_session("session1")
        await store.create_session("session2")
        
        sessions = await store.list_sessions()
        assert len(sessions) >= 2
    
    @pytest.mark.asyncio
    async def test_delete_session(self, store):
        """Test session deletion."""
        await store.ensure_dir()
        await store.create_session("delete_me")
        await store.append_message("delete_me", role="user", content="test")
        
        result = await store.delete_session("delete_me")
        assert result is True
        
        # Check it's gone
        info = await store.get_session_info("delete_me")
        assert info is None
    
    @pytest.mark.asyncio
    async def test_update_session(self, store):
        """Test session metadata update."""
        await store.ensure_dir()
        await store.create_session("update_me")
        
        result = await store.update_session(
            "update_me",
            {"messageCount": 10, "custom_field": "value"}
        )
        
        assert result is True
        
        info = await store.get_session_info("update_me")
        assert info["messageCount"] == 10
        assert info["custom_field"] == "value"
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, store):
        """Test getting nonexistent session."""
        info = await store.get_session_info("nonexistent")
        assert info is None
        
        transcript = await store.get_transcript("nonexistent")
        assert transcript == []
    
    @pytest.mark.asyncio
    async def test_clear_all(self, store):
        """Test clearing all sessions."""
        await store.ensure_dir()
        for i in range(5):
            await store.create_session(f"session_{i}")
        
        count = await store.clear_all()
        assert count == 5
        
        sessions = await store.list_sessions()
        assert len(sessions) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
