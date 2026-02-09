"""Tests for session pruning and compaction modules."""

import pytest
from datetime import datetime

from src.sessions.pruning import SessionPruner, SessionCompactor
from src.sessions.manager import session_manager


class TestSessionPruner:
    """Tests for SessionPruner class."""
    
    @pytest.fixture
    def pruner(self):
        """Create a pruner with test config."""
        return SessionPruner({
            "max_messages": 10,
            "max_tool_results": 5,
            "preserve_system_prompt": True,
            "preserve_user_messages": True,
        })
    
    @pytest.fixture
    async def populated_session(self):
        """Create a session with test data."""
        session_id = "test-prune-session"
        await session_manager.clear_history(session_id)
        
        # Add system message
        await session_manager.add_message(session_id, "system", "You are a helpful assistant.")
        
        # Add many user/assistant messages
        for i in range(25):
            await session_manager.add_message(session_id, "user", f"User message {i}")
            await session_manager.add_message(session_id, "assistant", f"Assistant response {i}")
        
        # Add some tool messages
        for i in range(8):
            await session_manager.add_message(
                session_id, 
                "tool", 
                f'{{"tool": "test_tool", "result": "result {i}"}}'
            )
        
        return session_id
    
    @pytest.mark.asyncio
    async def test_should_prune_small_session(self, pruner):
        """Test that small sessions are not pruned."""
        session_id = "small-session-test"
        await session_manager.clear_history(session_id)
        await session_manager.add_message(session_id, "user", "Hello")
        
        assert await pruner.should_prune(session_id) is False
    
    @pytest.mark.asyncio
    async def test_should_prune_large_session(self, pruner, populated_session):
        """Test that large sessions are pruned."""
        assert await pruner.should_prune(populated_session) is True
    
    @pytest.mark.asyncio
    async def test_prune_preserves_recent(self, pruner, populated_session):
        """Test that pruning preserves recent messages."""
        result = await pruner.prune(populated_session)
        
        assert result["pruned"] is True
        assert result["remaining_count"] <= 10
        
        # Check recent messages are preserved
        session = await session_manager.get_session(populated_session)
        history = session["history"]
        
        # Last messages should be the most recent ones
        assert "User message 24" in history[-1]["content"] or \
               "Assistant response 24" in history[-1]["content"]
    
    @pytest.mark.asyncio
    async def test_prune_counts(self, pruner, populated_session):
        """Test pruning statistics."""
        session = await session_manager.get_session(populated_session)
        original_count = len(session["history"])
        
        result = await pruner.prune(populated_session)
        
        assert result["pruned"] is True
        assert result["original_count"] == original_count
        assert result["pruned_count"] > 0
        assert result["remaining_count"] < original_count
    
    @pytest.mark.asyncio
    async def test_prune_no_op_for_small(self, pruner):
        """Test that pruning small sessions does nothing."""
        session_id = "small-prune-test"
        await session_manager.clear_history(session_id)
        await session_manager.add_message(session_id, "user", "Hello")
        
        result = await pruner.prune(session_id)
        
        assert result["pruned"] is False
        assert result["reason"] == "session_within_limits"


class TestSessionCompactor:
    """Tests for SessionCompactor class."""
    
    @pytest.fixture
    def compactor(self):
        """Create a compactor."""
        return SessionCompactor()
    
    @pytest.fixture
    async def long_session(self):
        """Create a session with many messages."""
        session_id = "test-compact-session"
        await session_manager.clear_history(session_id)
        
        for i in range(20):
            await session_manager.add_message(session_id, "user", f"User message {i}")
            await session_manager.add_message(session_id, "assistant", f"Assistant response {i}")
        
        return session_id
    
    @pytest.mark.asyncio
    async def test_compact_short_session(self, compactor):
        """Test that short sessions are not compacted."""
        session_id = "short-compact-test"
        await session_manager.clear_history(session_id)
        await session_manager.add_message(session_id, "user", "Hello")
        
        result = await compactor.compact(session_id)
        
        assert result["compact"] is False
        assert result["reason"] == "session_too_short"
    
    @pytest.mark.asyncio
    async def test_compact_creates_summary(self, compactor, long_session):
        """Test that compaction creates a summary message."""
        result = await compactor.compact(long_session)
        
        assert result["compact"] is True
        assert result["original_messages"] > 0
        assert "summary_length" in result
        
        # Check summary message was added
        session = await session_manager.get_session(long_session)
        history = session["history"]
        
        # First message should be the summary
        assert history[0]["role"] == "system"
        assert "compaction_summary" in history[0]["metadata"]
    
    @pytest.mark.asyncio
    async def test_compact_recent_messages_preserved(self, compactor, long_session):
        """Test that recent messages are preserved after compaction."""
        await compactor.compact(long_session)
        
        session = await session_manager.get_session(long_session)
        history = session["history"]
        
        # Last messages should be recent ones
        recent_content = history[-1]["content"]
        assert "User message 19" in recent_content or \
               "Assistant response 19" in recent_content


class TestSessionPruningIntegration:
    """Integration tests for pruning with manager."""
    
    @pytest.mark.asyncio
    async def test_prune_updates_session_manager(self):
        """Test that pruning properly updates session manager."""
        from src.sessions.pruning import session_pruner
        
        session_id = "integration-test-session"
        await session_manager.clear_history(session_id)
        
        # Add many messages
        for i in range(30):
            await session_manager.add_message(session_id, "user", f"Message {i}")
        
        # Get count before prune
        before = len(await session_manager.get_history(session_id))
        
        # Prune
        result = await session_pruner.prune(session_id)
        
        # Verify
        after = len(await session_manager.get_history(session_id))
        
        assert result["pruned"] is True
        assert after < before
        assert after <= session_pruner.config["max_messages"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
