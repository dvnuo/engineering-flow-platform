"""Tests for LLM client."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from openclaw_mini.agent.llm import LLMClient


class MockResponse:
    """Mock HTTP response."""
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
    
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "Error",
                request=MagicMock(),
                response=self
            )
    
    async def json(self):
        return self.json_data


@pytest.fixture
def llm_client():
    """Create a fresh LLM client."""
    client = LLMClient()
    client.api_key = "test_key"
    client.model = "gpt-3.5-turbo"
    client.max_retries = 1
    client.retry_delay = 0.01
    return client


class TestLLMClientSuccess:
    """Successful LLM client tests."""

    @pytest.mark.asyncio
    async def test_chat_success(self, llm_client):
        """Test successful chat completion."""
        mock_response = {
            "choices": [{"message": {"content": "Hello!"}}]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.chat(
                [{"role": "user", "content": "hi"}],
                "You are helpful"
            )
            assert result == "Hello!"

    @pytest.mark.asyncio
    async def test_chat_with_system_prompt(self, llm_client):
        """Test chat with system prompt included."""
        mock_response = {
            "choices": [{"message": {"content": "I am a helpful assistant."}}]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.chat(
                [{"role": "user", "hello"}],
                system_prompt="You are a robot."
            )
            assert result == "I am a helpful assistant."

    @pytest.mark.asyncio
    async def test_chat_empty_response(self, llm_client):
        """Test chat with empty response."""
        mock_response = {"choices": []}
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.chat([{"role": "user", "content": "hi"}])
            assert result == ""

    @pytest.mark.asyncio
    async def test_chat_response_no_message(self, llm_client):
        """Test chat response without message field."""
        mock_response = {"choices": [{}]}
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.chat([{"role": "user", "content": "hi"}])
            assert result == ""


class TestLLMClientRetry:
    """LLM client retry logic tests."""

    @pytest.mark.asyncio
    async def test_chat_with_retry(self, llm_client):
        """Test chat with retry on failure."""
        mock_response = {
            "choices": [{"message": {"content": "Success after retry"}}]
        }
        
        call_count = 0
        
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.RequestError("Connection failed")
            return MockResponse(mock_response)
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.side_effect = mock_post
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.chat([{"role": "user", "content": "test"}])
            assert result == "Success after retry"
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_chat_max_retries_exceeded(self, llm_client):
        """Test chat fails after max retries."""
        llm_client.max_retries = 2
        call_count = 0
        
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.RequestError("Persistent connection failure")
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.side_effect = mock_post
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            with pytest.raises(httpx.RequestError):
                await llm_client.chat([{"role": "user", "content": "test"}])
            
            assert call_count == 3  # Initial + 2 retries


class TestLLMClientErrorHandling:
    """LLM client error handling tests."""

    @pytest.mark.asyncio
    async def test_chat_http_error(self, llm_client):
        """Test chat handles HTTP errors."""
        llm_client.max_retries = 1
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(
                {"error": {"message": "Invalid API key"}},
                status_code=401
            )
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            with pytest.raises(httpx.HTTPStatusError):
                await llm_client.chat([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_chat_rate_limit(self, llm_client):
        """Test chat handles rate limiting."""
        llm_client.max_retries = 1
        llm_client.retry_delay = 0.01
        
        call_count = 0
        
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                # Simulate rate limit
                raise httpx.HTTPStatusError(
                    "Rate limit",
                    request=MagicMock(),
                    response=MockResponse({"error": "rate limit"}, status_code=429)
                )
            return MockResponse({"choices": [{"message": {"content": "Success"}}]})
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.side_effect = mock_post
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.chat([{"role": "user", "content": "test"}])
            assert result == "Success"
            assert call_count == 2


class TestLLMClientComplete:
    """LLM completion tests."""

    @pytest.mark.asyncio
    async def test_complete_success(self, llm_client):
        """Test successful completion."""
        mock_response = {
            "choices": [{"text": "Completion result"}]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.complete("Write a story about")
            assert result == "Completion result"

    @pytest.mark.asyncio
    async def test_complete_with_retry(self, llm_client):
        """Test completion with retry."""
        mock_response = {
            "choices": [{"text": "Retry success"}]
        }
        
        call_count = 0
        
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.RequestError("Connection failed")
            return MockResponse(mock_response)
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.side_effect = mock_post
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.complete("Test prompt")
            assert result == "Retry success"
            assert call_count == 2


class TestLLMClientEdgeCases:
    """LLM client edge case tests."""

    @pytest.mark.asyncio
    async def test_chat_empty_history(self, llm_client):
        """Test chat with empty message history."""
        mock_response = {
            "choices": [{"message": {"content": "Response to empty"}}]
        }
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.chat([], "System prompt")
            assert result == "Response to empty"

    @pytest.mark.asyncio
    async def test_chat_long_history(self, llm_client):
        """Test chat with long message history."""
        mock_response = {
            "choices": [{"message": {"content": "Response"}}]
        }
        
        # Create long history
        long_history = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.chat(long_history)
            assert result == "Response"

    @pytest.mark.asyncio
    async def test_chat_special_content(self, llm_client):
        """Test chat with special characters in content."""
        mock_response = {
            "choices": [{"message": {"content": "Response with special chars"}}]
        }
        
        special_history = [
            {"role": "user", "content": "Hello with emoji 🌍 and 中文\n\tTab"}
        ]
        
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await llm_client.chat(special_history)
            assert result == "Response with special chars"


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_chat_success())
    asyncio.run(test_chat_with_retry())
    asyncio.run(test_chat_empty_response())
    print("All LLM client tests passed!")
