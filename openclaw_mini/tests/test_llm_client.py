"""Tests for LLM client."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import AsyncMock, patch
import httpx

from openclaw_mini.agent.llm import LLMClient


class MockResponse:
    """Mock HTTP response."""
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
    
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("Error", request=None, response=self)
    
    async def json(self):
        return self.json_data


@pytest.mark.asyncio
async def test_chat_success():
    """Test successful chat completion."""
    client = LLMClient()
    client.api_key = "test_key"
    client.model = "gpt-3.5-turbo"
    
    mock_response = {
        "choices": [{"message": {"content": "Hello!"}}]
    }
    
    with patch.object(client, 'max_retries', 0):
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await client.chat([{"role": "user", "content": "hi"}], "You are helpful")
            assert result == "Hello!"


@pytest.mark.asyncio
async def test_chat_with_retry():
    """Test chat with retry on failure."""
    client = LLMClient()
    client.api_key = "test_key"
    client.model = "gpt-3.5-turbo"
    client.max_retries = 2
    client.retry_delay = 0.01
    
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
        
        result = await client.chat([{"role": "user", "content": "test"}])
        assert result == "Success after retry"
        assert call_count == 2


@pytest.mark.asyncio
async def test_chat_empty_response():
    """Test chat with empty response."""
    client = LLMClient()
    client.api_key = "test_key"
    client.model = "gpt-3.5-turbo"
    
    mock_response = {"choices": []}
    
    with patch.object(client, 'max_retries', 0):
        with patch('httpx.AsyncClient') as mock_client:
            mock_session = AsyncMock()
            mock_session.post.return_value.__aenter__.return_value = MockResponse(mock_response)
            mock_session.post.return_value.__aexit__.return_value = None
            mock_client.return_value = mock_session
            
            result = await client.chat([{"role": "user", "content": "hi"}])
            assert result == ""


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_chat_success())
    asyncio.run(test_chat_with_retry())
    asyncio.run(test_chat_empty_response())
    print("All LLM client tests passed!")
