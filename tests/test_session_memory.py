"""Tests for session memory hook"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock


class TestSessionMemoryHook:
    """Tests for session memory hook functionality"""
    
    def test_generate_slug_from_user_message(self):
        """Test slug generation from user message"""
        from src.hooks.session_memory import _generate_slug
        
        messages = [
            {"role": "user", "content": "Help me fix the bug in login"},
            {"role": "assistant", "content": "I'll help you fix the login bug"}
        ]
        
        slug = _generate_slug(messages)
        
        # Should contain keywords from user message
        assert "help" in slug or "fix" in slug or "bug" in slug or "login" in slug
    
    def test_generate_slug_fallback(self):
        """Test slug fallback when no user message"""
        from src.hooks.session_memory import _generate_slug
        
        messages = [
            {"role": "assistant", "content": "Hello, how can I help?"}
        ]
        
        slug = _generate_slug(messages)
        
        # Should be time-based fallback
        assert len(slug) == 4  # HHMM format
    
    def test_generate_slug_empty(self):
        """Test slug generation with empty messages"""
        from src.hooks.session_memory import _generate_slug
        
        slug = _generate_slug([])
        
        # Should be time-based fallback
        assert len(slug) == 4
    
    def test_build_memory_content(self):
        """Test memory content building"""
        from src.hooks.session_memory import _build_memory_content
        
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ]
        
        content = _build_memory_content("test-session", messages)
        
        assert "Session:" in content
        assert "test-session" in content
        assert "USER" in content
        assert "Hello" in content
        assert "ASSISTANT" in content
        assert "Hi there!" in content


class TestSessionMemoryAPIs:
    """Tests for session memory API endpoints"""
    
    @pytest.mark.asyncio
    async def test_save_session_to_memory(self):
        """Test save_session_to_memory function"""
        with patch('src.hooks.session_memory.session_manager') as mock_manager:
            mock_session = {
                "history": [
                    {"role": "user", "content": "Test message"},
                    {"role": "assistant", "content": "Test response"}
                ]
            }
            mock_manager.get_session = AsyncMock(return_value=mock_session)
            
            from src.hooks.session_memory import save_session_to_memory
            
            with patch('builtins.open', MagicMock()):
                with patch('pathlib.Path.mkdir', MagicMock()):
                    result = await save_session_to_memory("test-session")
                    
                    # Should have called get_session
                    mock_manager.get_session.assert_called_once_with("test-session")
    
    @pytest.mark.asyncio
    async def test_save_and_clear_session(self):
        """Test save_and_clear_session function"""
        with patch('src.hooks.session_memory.session_manager') as mock_manager:
            mock_session = {
                "history": [
                    {"role": "user", "content": "Test message"}
                ]
            }
            mock_manager.get_session = AsyncMock(return_value=mock_session)
            mock_manager.clear_history = AsyncMock()
            
            from src.hooks.session_memory import save_and_clear_session
            
            with patch('builtins.open', MagicMock()):
                with patch('pathlib.Path.mkdir', MagicMock()):
                    result = await save_and_clear_session("test-session")
                    
                    # Should have saved and cleared
                    mock_manager.get_session.assert_called_once()
                    mock_manager.clear_history.assert_called_once_with("test-session")
                    assert result["success"] is True
