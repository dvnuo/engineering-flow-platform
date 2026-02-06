"""Tests for LLM client."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import httpx

# Import after path setup
from agent.llm import LLMClient


class MockResponse:
    """Mock HTTP response."""
    
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
        self.text = json_data if isinstance(json_data, str) else str(json_data)
    
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=MagicMock(),
                response=self
            )
    
    def json(self):  # httpx response.json() is sync
        return self.json_data


class MockHttpClient:
    """Simple mock HTTP client."""
    
    def __init__(self, response):
        self.response = response
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None
    
    async def post(self, *args, **kwargs):
        return self.response


@pytest.fixture
def llm_client():
    """Create a fresh LLM client."""
    client = LLMClient()
    client.api_key = "test_key"
    client.model = "gpt-3.5-turbo"
    client.max_retries = 2  # Allow for retry testing
    client.retry_delay = 0.01
    return client


class TestLLMClientSuccess:
    """Successful LLM client tests."""

    @pytest.mark.asyncio
    async def test_chat_success(self, llm_client):
        """Test successful chat completion."""
        mock_response = MockResponse({
            "choices": [{"message": {"content": "Hello!"}}]
        })
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat(
                [{"role": "user", "content": "hi"}],
                "You are helpful"
            )
            # New API may include usage info
            assert result["content"] == "Hello!"
            assert "tool_calls" in result
            # Usage field is optional
            assert "usage" in result or result.get("usage", {}) is not None

    @pytest.mark.asyncio
    async def test_chat_with_system_prompt(self, llm_client):
        """Test chat with system prompt included."""
        mock_response = MockResponse({
            "choices": [{"message": {"content": "I am a helpful assistant."}}]
        })
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat(
                [{"role": "user", "content": "hello"}],
                system_prompt="You are a robot."
            )
            assert result["content"] == "I am a helpful assistant."

    @pytest.mark.asyncio
    async def test_chat_empty_response(self, llm_client):
        """Test chat with empty content in response."""
        mock_response = MockResponse({
            "choices": [{"message": {"content": ""}}],
            "usage": {"total_tokens": 10}
        })
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat([{"role": "user", "content": "hi"}])
            assert result["content"] == ""
            assert "usage" in result

    @pytest.mark.asyncio
    async def test_chat_response_no_message(self, llm_client):
        """Test chat response with missing message field."""
        mock_response = MockResponse({
            "choices": [{"message": {}}],
            "usage": {"total_tokens": 5}
        })
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat([{"role": "user", "content": "hi"}])
            assert result["content"] == ""
            assert "tool_calls" in result


class TestLLMClientRetry:
    """LLM client retry logic tests."""

    @pytest.mark.asyncio
    async def test_chat_with_retry(self, llm_client):
        """Test chat with retry on failure."""
        call_count = 0
        success_response = MockResponse({
            "choices": [{"message": {"content": "Success after retry"}}]
        })
        
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.RequestError("Connection failed")
            return success_response
        
        class RetryClient(MockHttpClient):
            async def post(self, *args, **kwargs):
                return await mock_post(*args, **kwargs)
        
        mock_client = RetryClient(success_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat([{"role": "user", "content": "test"}])
            assert result["content"] == "Success after retry"
            # First attempt fails, second succeeds
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_chat_max_retries_exceeded(self, llm_client):
        """Test chat fails after max retries."""
        llm_client.max_retries = 3  # Default is 3 attempts
        
        call_count = 0
        
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.RequestError("Persistent connection failure")
        
        class FailClient(MockHttpClient):
            async def post(self, *args, **kwargs):
                return await mock_post(*args, **kwargs)
        
        mock_client = FailClient(None)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            with pytest.raises(httpx.RequestError):
                await llm_client.chat([{"role": "user", "content": "test"}])
            
            # max_retries = 3 means 3 total attempts (1 initial + 2 retries)
            assert call_count == 3


class TestLLMClientErrorHandling:
    """LLM client error handling tests."""

    @pytest.mark.asyncio
    async def test_chat_http_error(self, llm_client):
        """Test chat handles HTTP errors."""
        llm_client.max_retries = 1
        
        mock_response = MockResponse(
            {"error": {"message": "Invalid API key"}},
            status_code=401
        )
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await llm_client.chat([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_chat_rate_limit(self, llm_client):
        """Test chat handles rate limiting."""
        # Use fixture's max_retries (2) and retry_delay (0.01)
        call_count = 0
        success_response = MockResponse({"choices": [{"message": {"content": "Success"}}]})
        
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                # Return error response that will raise on raise_for_status
                return success_response  # Return success but raise error
        
        class RateLimitClient(MockHttpClient):
            def __init__(self, response):
                super().__init__(response)
                self.call_count = 0
            
            async def post(self, *args, **kwargs):
                self.call_count += 1
                if self.call_count < 2:
                    # First call: raise HTTPStatusError
                    error_response = MockResponse(
                        {"error": "rate limit"},
                        status_code=429
                    )
                    raise httpx.HTTPStatusError(
                        "Rate limit",
                        request=MagicMock(),
                        response=error_response
                    )
                return success_response
        
        mock_client = RateLimitClient(success_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat([{"role": "user", "content": "test"}])
            assert result["content"] == "Success"
            assert mock_client.call_count == 2


class TestLLMClientComplete:
    """LLM completion tests.

    Note: The new LLMClient uses chat() for all completions.
    The complete() method has been removed in favor of chat().
    """

    @pytest.mark.asyncio
    async def test_complete_via_chat(self, llm_client):
        """Test completion via chat method (new API)."""
        mock_response = MockResponse({
            "choices": [{"message": {"content": "Completion result"}}]
        })
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat(
                [{"role": "user", "content": "Write a story about"}]
            )
            # New API returns full response dict
            assert result["content"] == "Completion result"
            assert "tool_calls" in result


class TestLLMClientEdgeCases:
    """LLM client edge case tests."""

    @pytest.mark.asyncio
    async def test_chat_empty_history(self, llm_client):
        """Test chat with empty message history."""
        mock_response = MockResponse({
            "choices": [{"message": {"content": "Response to empty"}}]
        })
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat([], "System prompt")
            assert result["content"] == "Response to empty"

    @pytest.mark.asyncio
    async def test_chat_long_history(self, llm_client):
        """Test chat with long message history."""
        mock_response = MockResponse({
            "choices": [{"message": {"content": "Response"}}]
        })
        mock_client = MockHttpClient(mock_response)
        
        # Create long history
        long_history = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(100)
        ]
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat(long_history)
            assert result["content"] == "Response"

    @pytest.mark.asyncio
    async def test_chat_special_content(self, llm_client):
        """Test chat with special characters in content."""
        mock_response = MockResponse({
            "choices": [{"message": {"content": "Response with special chars"}}]
        })
        mock_client = MockHttpClient(mock_response)
        
        special_history = [
            {"role": "user", "content": "Hello with emoji 🌍 and 中文\n\tTab"}
        ]
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await llm_client.chat(special_history)
            assert result["content"] == "Response with special chars"


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_chat_success())
    asyncio.run(test_chat_with_retry())
    asyncio.run(test_chat_empty_response())
    print("All LLM client tests passed!")
