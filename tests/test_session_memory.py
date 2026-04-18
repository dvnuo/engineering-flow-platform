"""Tests for session memory (summarization)"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock


class TestSessionMemory:
    """Tests for session summarization"""
    
    @pytest.mark.asyncio
    async def test_summarize_session(self):
        """Test session summarization"""
        from src.hooks.session_memory import summarize_session
        
        messages = [
            {"role": "user", "content": "Help me fix the login bug"},
            {"role": "assistant", "content": "I'll help you fix it. Let me check the code."},
            {"role": "tool", "content": "File: auth.py - line 42 fixed"},
            {"role": "assistant", "content": "Fixed the bug!"},
        ]
        
        summary = await summarize_session("test-session", messages)
        
        assert "User Request" in summary or "Login" in summary or "bug" in summary.lower()
    
    @pytest.mark.asyncio
    async def test_summarize_empty_session(self):
        """Test summarizing empty session"""
        from src.hooks.session_memory import summarize_session
        
        summary = await summarize_session("test-session", [])
        assert summary == ""
    
    @pytest.mark.asyncio
    async def test_summarize_short_session(self):
        """Test summarizing short session (too short to summarize)"""
        from src.hooks.session_memory import summarize_session
        
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        
        summary = await summarize_session("test-session", messages)
        # Short sessions still get summarized with this approach
        assert summary is not None


class TestSaveSessionSummary:
    """Tests for save_session_summary function"""
    
    @pytest.mark.asyncio
    async def test_save_session_summary(self):
        """Test saving session summary"""
        with patch('src.hooks.session_memory.session_manager') as mock_manager:
            mock_session = {
                "history": [
                    {"role": "user", "content": "Test message"},
                    {"role": "assistant", "content": "Test response"}
                ],
                "channel": "webchat",
                "created_at": "2026-02-26T12:00:00"
            }
            mock_manager.get_session = AsyncMock(return_value=mock_session)
            
            with patch('builtins.open', MagicMock()):
                with patch('pathlib.Path.mkdir', MagicMock()):
                    from src.hooks.session_memory import save_session_summary
                    
                    # Patch the file write
                    with patch('builtins.open', MagicMock()):
                        with patch('pathlib.Path.exists', return_value=False):
                            result = await save_session_summary("test-session")
                            
                            # Should have called get_session
                            mock_manager.get_session.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_save_empty_session(self):
        """Test saving empty session returns None"""
        with patch('src.hooks.session_memory.session_manager') as mock_manager:
            mock_session = {"history": [], "channel": "webchat"}
            mock_manager.get_session = AsyncMock(return_value=mock_session)
            
            from src.hooks.session_memory import save_session_summary
            result = await save_session_summary("test-session")
            
            # Should return None for empty session
            assert result is None

    @pytest.mark.asyncio
    async def test_save_session_summary_prefers_context_state(self):
        """When context_state exists, memory summary should use it over heuristic summary."""
        with patch('src.hooks.session_memory.session_manager') as mock_manager:
            mock_session = {
                "history": [
                    {"role": "user", "content": "Initial objective"},
                    {"role": "assistant", "content": "Response"},
                    {"role": "user", "content": "Follow up"},
                ],
                "metadata": {
                    "context_state": {
                        "objective": "Migrate service safely",
                        "current_state": "Validation in progress",
                        "constraints": ["must avoid downtime"],
                        "next_step": "Run final verification",
                    }
                },
                "channel": "webchat",
                "created_at": "2026-02-26T12:00:00",
            }
            mock_manager.get_session = AsyncMock(return_value=mock_session)

            from src.hooks.session_memory import save_session_summary

            with patch('src.hooks.session_memory.summarize_session', new=AsyncMock(return_value="heuristic summary")) as mock_summarize:
                with patch('builtins.open', MagicMock()):
                    with patch('pathlib.Path.mkdir', MagicMock()):
                        with patch('pathlib.Path.exists', return_value=False):
                            await save_session_summary("test-session")

            mock_summarize.assert_not_called()


class TestBuildSessionEntry:
    """Tests for _build_session_entry"""
    
    def test_build_session_entry(self):
        """Test building session entry"""
        from src.hooks.session_memory import _build_session_entry
        
        entry = _build_session_entry(
            session_id="test-123",
            channel="webchat",
            created_at="2026-02-26T12:00:00",
            summary="Fixed the login bug"
        )
        
        assert "Session:" in entry
        assert "webchat" in entry
        assert "Fixed the login bug" in entry
