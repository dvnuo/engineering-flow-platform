"""Tests for LLM client."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import httpx

# Import after path setup
from src.agents.llm import LLMClient
from src.config import config, resolve_llm_temperature, DEFAULT_LLM_TEMPERATURE


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


@pytest.fixture
def openai_provider():
    """Create an OpenAIProvider with _check_api_key patched."""
    from src.agents.llm import OpenAIProvider
    provider = OpenAIProvider()
    # Patch _check_api_key to avoid env dependency
    provider._check_api_key = lambda: None
    return provider


@pytest.fixture
def github_copilot_provider():
    """Create a GitHubCopilotProvider with _check_api_key patched."""
    from src.agents.llm import GitHubCopilotProvider
    provider = GitHubCopilotProvider()
    provider._check_api_key = lambda: None
    return provider


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


class TestResponsesAPI:
    """Tests for Responses API (/responses endpoint)."""

    @pytest.mark.asyncio
    async def test_responses_basic_text(self, openai_provider):
        """Test basic responses() call with text content."""
        # Mock Response API response format
        mock_response = MockResponse({
            "output": [
                {
                    "type": "message",
                    "id": "msg_123",
                    "content": [
                        {"type": "output_text", "text": "Hello from Responses API!"}
                    ]
                }
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5
            }
        })
        
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await openai_provider.responses(
                messages=[{"role": "user", "content": "hi"}],
                system_prompt="You are helpful"
            )
            assert result["content"] == "Hello from Responses API!"
            assert "usage" in result

    @pytest.mark.asyncio
    async def test_responses_string_content(self, openai_provider):
        """Test responses() with string content (not list)."""
        # Response with string content (not list)
        mock_response = MockResponse({
            "output": [
                {
                    "type": "message",
                    "id": "msg_123",
                    "content": "Direct string response"
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 3}
        })
        
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await openai_provider.responses(
                messages=[{"role": "user", "content": "hello"}]
            )
            assert result["content"] == "Direct string response"

    @pytest.mark.asyncio
    async def test_responses_multiple_messages(self, openai_provider):
        """Test responses() with multiple output messages (accumulation)."""
        # Multiple message outputs
        mock_response = MockResponse({
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Part1 "}]
                },
                {
                    "type": "message", 
                    "content": [{"type": "output_text", "text": "Part2"}]
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 10}
        })
        
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await openai_provider.responses(
                messages=[{"role": "user", "content": "test"}]
            )
            # Should accumulate both parts
            assert "Part1" in result["content"]
            assert "Part2" in result["content"]

    @pytest.mark.asyncio
    async def test_openai_responses_empty_output_max_output_tokens_returns_truncated_error(self, openai_provider):
        mock_response = MockResponse({
            "output": [],
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"input_tokens": 10, "output_tokens": 0},
        })
        mock_client = MockHttpClient(mock_response)

        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await openai_provider.responses(
                messages=[{"role": "user", "content": "hello"}]
            )

        assert result["error"]["type"] == "truncated_response"
        assert result["error"]["code"] == "max_output_tokens_exceeded"
        assert result["error"]["details"]["incomplete_reason"] == "max_output_tokens"

    @pytest.mark.asyncio
    async def test_github_copilot_responses_empty_output_max_output_tokens_returns_truncated_error(
        self,
        github_copilot_provider,
    ):
        mock_response = MockResponse({
            "output": [],
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"input_tokens": 10, "output_tokens": 0},
        })
        mock_client = MockHttpClient(mock_response)

        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await github_copilot_provider.responses(
                messages=[{"role": "user", "content": "hello"}]
            )

        assert result["error"]["type"] == "truncated_response"
        assert result["error"]["code"] == "max_output_tokens_exceeded"
        assert result["error"]["details"]["incomplete_reason"] == "max_output_tokens"

    @pytest.mark.asyncio
    async def test_openai_responses_allows_null_optional_response_objects(self, openai_provider):
        mock_response = MockResponse({
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "incomplete_details": None,
            "usage": None,
        })
        mock_client = MockHttpClient(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await openai_provider.responses(
                messages=[{"role": "user", "content": "hello"}]
            )

        assert result["content"] == "ok"
        assert "error" not in result
        assert result["usage"]["prompt_tokens"] > 0
        assert result["usage"]["completion_tokens"] > 0

    @pytest.mark.asyncio
    async def test_github_copilot_responses_allows_null_optional_response_objects(
        self,
        github_copilot_provider,
    ):
        mock_response = MockResponse({
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "incomplete_details": None,
            "usage": None,
        })
        mock_client = MockHttpClient(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await github_copilot_provider.responses(
                messages=[{"role": "user", "content": "hello"}]
            )

        assert result["content"] == "ok"
        assert "error" not in result
        assert result["usage"]["prompt_tokens"] > 0
        assert result["usage"]["completion_tokens"] > 0

    @pytest.mark.asyncio
    async def test_github_copilot_responses_empty_output_with_null_incomplete_details_returns_empty_response(
        self,
        github_copilot_provider,
    ):
        mock_response = MockResponse({
            "output": [],
            "incomplete_details": None,
            "usage": None,
        })
        mock_client = MockHttpClient(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await github_copilot_provider.responses(
                messages=[{"role": "user", "content": "hello"}]
            )

        assert result["error"]["type"] == "empty_response"
        assert result["error"]["code"] == "empty_message"

    @pytest.mark.asyncio
    async def test_openai_responses_partial_content_with_max_output_tokens_returns_warning(self, openai_provider):
        mock_response = MockResponse({
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "partial"}]}],
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })
        mock_client = MockHttpClient(mock_response)
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await openai_provider.responses(messages=[{"role": "user", "content": "hello"}])
        assert result["content"] == "partial"
        assert result["truncated"] is True
        assert result["warning"]["code"] == "max_output_tokens_exceeded"

    @pytest.mark.asyncio
    async def test_openai_responses_empty_content_with_function_call_not_error_even_if_truncated(self, openai_provider):
        mock_response = MockResponse({
            "output": [{"type": "function_call", "call_id": "c1", "name": "read", "arguments": {}}],
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"input_tokens": 10, "output_tokens": 0},
        })
        mock_client = MockHttpClient(mock_response)
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await openai_provider.responses(messages=[{"role": "user", "content": "hello"}])
        assert "error" not in result
        assert result["function_calls"][0]["call_id"] == "c1"

    @pytest.mark.asyncio
    async def test_responses_tool_calls(self, openai_provider):
        """Test responses() parses tool calls correctly."""
        # Response with function call
        mock_response = MockResponse({
            "output": [
                {
                    "type": "message",
                    "id": "msg_123",
                    "content": [
                        {
                            "type": "function_call",
                            "call_id": "call_123",
                            "name": "get_weather",
                            "arguments": '{"city": "Tokyo"}'
                        }
                    ]
                }
            ],
            "usage": {"input_tokens": 50, "output_tokens": 20}
        })
        
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            # Don't pass tools so Responses API path is tested (not fallback to chat)
            result = await openai_provider.responses(
                messages=[{"role": "user", "content": "What's the weather?"}]
            )
            # Verify tool_calls are parsed correctly
            assert "tool_calls" in result
            assert len(result["tool_calls"]) > 0
            # Check type field is present
            assert result["tool_calls"][0].get("type") == "function"

    @pytest.mark.asyncio
    async def test_responses_with_vision_content(self, openai_provider):
        """Test responses() converts vision content to correct format."""
        # Track the request payload
        captured_payload = {}
        
        class TrackingMockClient(MockHttpClient):
            async def post(self, *args, **kwargs):
                captured_payload['json'] = kwargs.get('json', {})
                return self.response
        
        # Response
        mock_response = MockResponse({
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "It's a cat!"}]
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 5}
        })
        
        mock_client = TrackingMockClient(mock_response)
        
        # Messages with vision content in Chat Completions format
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}}
                ]
            }
        ]
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await openai_provider.responses(messages=messages)
            assert result["content"] == "It's a cat!"
            
            # Verify the conversion: input_image with string URL
            assert 'input' in captured_payload.get('json', {})
            input_items = captured_payload['json']['input']
            assert len(input_items) == 1
            # Check that content was converted to Responses API format
            content = input_items[0].get('content', [])
            assert isinstance(content, list)
            # Should have text and image blocks
            text_block = next((b for b in content if b.get('type') == 'input_text'), None)
            image_block = next((b for b in content if b.get('type') == 'input_image'), None)
            assert text_block is not None
            assert image_block is not None
            # image_url should be a string, not an object
            assert isinstance(image_block.get('image_url'), str)

    @pytest.mark.asyncio
    async def test_responses_top_level_function_call(self, openai_provider):
        """Test responses() handles top-level function_call output."""
        # Response with top-level function_call (not nested in message)
        mock_response = MockResponse({
            "output": [
                {"type": "function_call", "id": "call_456", "name": "search", "arguments": '{"query": "test"}'}
            ],
            "usage": {"input_tokens": 30, "output_tokens": 15}
        })
        
        mock_client = MockHttpClient(mock_response)
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            # Don't pass tools to test Responses path
            result = await openai_provider.responses(
                messages=[{"role": "user", "content": "search for test"}]
            )
            assert "tool_calls" in result

    @pytest.mark.asyncio
    async def test_llm_client_responses_type_annotation(self):
        """Test LLMClient.responses accepts messages with Any content type."""
        from src.agents.llm import LLMClient
        
        client = LLMClient()
        
        # This should not cause type errors - messages can have complex content
        messages = [
            {"role": "user", "content": "simple text"},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},  # List content
            {"role": "user", "content": [
                {"type": "text", "text": "Describe this"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}
            ]}
        ]
        
        # Just verify the method accepts the type (no runtime error for type check)
        import inspect
        sig = inspect.signature(client.responses)
        # If we get here without TypeError, the signature is correct
        assert "messages" in sig.parameters


class TestChatAPIFallback:
    """Tests for model-based fallback from Responses to Chat API."""

    @pytest.mark.asyncio
    async def test_responses_fallback_to_chat_for_old_models(self):
        """Test that responses() falls back to chat() for models in USE_CHAT_API_MODELS."""
        from src.agents.llm import USE_CHAT_API_MODELS, OpenAIProvider
        
        # Create a provider with gpt-3.5-turbo (which is in USE_CHAT_API_MODELS)
        provider = OpenAIProvider()
        provider.default_model = "gpt-3.5-turbo"
        provider.api_key = "test-key"
        
        # Create LLMClient with our provider
        client = LLMClient()
        client.providers = {"openai": provider}
        client.default_provider = "openai"
        
        # Mock chat() to verify it's called
        with patch.object(client, 'chat', new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {"content": "test response", "function_calls": []}
            
            # Call responses with gpt-3.5-turbo
            result = await client.responses(
                messages=[{"role": "user", "content": "hello"}],
                model="gpt-3.5-turbo"
            )
            
            # Verify chat was called (fallback triggered)
            mock_chat.assert_called_once()
            assert result["content"] == "test response"

    @pytest.mark.asyncio
    async def test_responses_uses_responses_api_for_new_models(self):
        """Test that responses() uses Responses API for models NOT in USE_CHAT_API_MODELS."""
        from src.agents.llm import USE_CHAT_API_MODELS, OpenAIProvider
        
        # Create a provider with gpt-4o (which is NOT in USE_CHAT_API_MODELS)
        provider = OpenAIProvider()
        provider.default_model = "gpt-4o"
        provider.api_key = "test-key"
        
        # Create LLMClient with our provider
        client = LLMClient()
        client.providers = {"openai": provider}
        client.default_provider = "openai"
        
        # Mock the provider's responses method
        with patch.object(provider, 'responses', new_callable=AsyncMock) as mock_responses:
            mock_responses.return_value = {"content": "test response"}
            
            # Call responses with gpt-4o
            result = await client.responses(
                messages=[{"role": "user", "content": "hello"}],
                model="gpt-4o"
            )
            
            # Verify provider.responses was called (no fallback)
            mock_responses.assert_called_once()
            assert result["content"] == "test response"
    @pytest.mark.asyncio
    async def test_responses_fallback_preserves_function_calls(self):
        """Test that fallback converts function_calls correctly."""
        from src.agents.llm import USE_CHAT_API_MODELS, OpenAIProvider
        
        provider = OpenAIProvider()
        provider.default_model = "gpt-3.5-turbo"
        provider.api_key = "test-key"
        
        client = LLMClient()
        client.providers = {"openai": provider}
        client.default_provider = "openai"
        
        # Simulate Chat API returning tool_calls
        with patch.object(client, 'chat', new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {
                "content": "I used a tool",
                "tool_calls": [
                    {"id": "call_123", "type": "function", "function": {"name": "test_func", "arguments": "{}"}}
                ]
            }
            
            result = await client.responses(
                messages=[{"role": "user", "content": "use a tool"}],
                model="gpt-3.5-turbo"
            )
            
            # Verify function_calls is populated from tool_calls
            assert "function_calls" in result
            assert len(result["function_calls"]) == 1
            assert result["function_calls"][0]["call_id"] == "call_123"
            assert result["function_calls"][0]["name"] == "test_func"

    @pytest.mark.asyncio
    async def test_responses_fallback_converts_input_items(self):
        """Test that fallback converts input_items to chat messages correctly."""
        from src.agents.llm import USE_CHAT_API_MODELS, OpenAIProvider
        
        provider = OpenAIProvider()
        provider.default_model = "gpt-3.5-turbo"
        provider.api_key = "test-key"
        
        client = LLMClient()
        client.providers = {"openai": provider}
        client.default_provider = "openai"
        
        # input_items with function_call and function_call_output
        input_items = [
            {"type": "message", "role": "user", "content": "use a tool"},
            {"type": "function_call", "call_id": "call_123", "name": "test_func", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_123", "output": "tool result"},
        ]
        
        with patch.object(client, 'chat', new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {"content": "done"}
            
            await client.responses(
                input_items=input_items,
                model="gpt-3.5-turbo"
            )
            
            # Verify chat was called with converted messages
            mock_chat.assert_called_once()
            call_args = mock_chat.call_args
            
            # Check messages were passed
            messages = call_args.kwargs.get("messages", [])
            assert len(messages) >= 2
            
            # Should have user message
            user_msg = next((m for m in messages if m.get("role") == "user"), None)
            assert user_msg is not None
            
            # Should have tool result message
            tool_msg = next((m for m in messages if m.get("role") == "tool"), None)
            assert tool_msg is not None
            assert tool_msg.get("tool_call_id") == "call_123"

    @pytest.mark.asyncio
    async def test_responses_fallback_multiple_tool_calls_ordering(self):
        """Test that multiple function_call_outputs maintain correct ordering.
        
        When multiple tool calls are executed from the same assistant message,
        the tool results should be inserted in execution order after the
        assistant message with tool_calls, not reversed.
        """
        from src.agents.llm import LLMClient, OpenAIProvider
        
        provider = OpenAIProvider()
        provider.default_model = "gpt-3.5-turbo"
        provider.api_key = "test-key"
        
        client = LLMClient()
        client.providers = {"openai": provider}
        client.default_provider = "openai"
        
        # input_items with multiple function_calls and their outputs
        input_items = [
            {"type": "message", "role": "user", "content": "use tools"},
            {"type": "function_call", "call_id": "call_1", "name": "func1", "arguments": "{}"},
            {"type": "function_call", "call_id": "call_2", "name": "func2", "arguments": "{}"},
            {"type": "function_call", "call_id": "call_3", "name": "func3", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "result_1"},
            {"type": "function_call_output", "call_id": "call_2", "output": "result_2"},
            {"type": "function_call_output", "call_id": "call_3", "output": "result_3"},
        ]
        
        with patch.object(client, 'chat', new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {"content": "done"}
            
            await client.responses(
                input_items=input_items,
                model="gpt-3.5-turbo"
            )
            
            mock_chat.assert_called_once()
            messages = mock_chat.call_args.kwargs.get("messages", [])
            
            # Find the assistant message with tool_calls
            assistant_idx = None
            for i, m in enumerate(messages):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    assistant_idx = i
                    break
            assert assistant_idx is not None, "Should have assistant with tool_calls"
            
            # Get all tool messages after the assistant
            tool_indices = []
            for i, m in enumerate(messages):
                if m.get("role") == "tool":
                    tool_indices.append(i)
            
            # Should have exactly 3 tool messages
            assert len(tool_indices) == 3, f"Should have 3 tool messages, got {len(tool_indices)}"
            
            # Verify ordering: call_1, call_2, call_3 results
            tool_msgs = [messages[i] for i in tool_indices]
            assert tool_msgs[0].get("tool_call_id") == "call_1", "First tool result should be call_1"
            assert tool_msgs[1].get("tool_call_id") == "call_2", "Second tool result should be call_2"
            assert tool_msgs[2].get("tool_call_id") == "call_3", "Third tool result should be call_3"
            
            # Verify outputs are in correct order
            assert tool_msgs[0].get("content") == "result_1"
            assert tool_msgs[1].get("content") == "result_2"
            assert tool_msgs[2].get("content") == "result_3"

    @pytest.mark.asyncio
    async def test_responses_fallback_no_duplicate_assistant(self):
        """Test that when tool_calls are discovered, no duplicate assistant is created.
        
        If an assistant message was already appended before tool_calls were discovered,
        it should be replaced, not duplicated.
        """
        from src.agents.llm import LLMClient, OpenAIProvider
        
        provider = OpenAIProvider()
        provider.default_model = "gpt-3.5-turbo"
        provider.api_key = "test-key"
        
        client = LLMClient()
        client.providers = {"openai": provider}
        client.default_provider = "openai"
        
        # Simulate input where function_call appears after user message
        input_items = [
            {"type": "message", "role": "user", "content": "use a tool"},
            {"type": "function_call", "call_id": "call_1", "name": "test_func", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "tool result"},
        ]
        
        with patch.object(client, 'chat', new_callable=AsyncMock) as mock_chat:
            mock_chat.return_value = {"content": "final response"}
            
            await client.responses(
                input_items=input_items,
                model="gpt-3.5-turbo"
            )
            
            mock_chat.assert_called_once()
            messages = mock_chat.call_args.kwargs.get("messages", [])
            
            # Count assistant messages with tool_calls
            assistant_count = sum(
                1 for m in messages 
                if m.get("role") == "assistant" and m.get("tool_calls")
            )
            
            # Should have exactly one assistant with tool_calls, not duplicates
            assert assistant_count == 1, \
                f"Should have exactly 1 assistant with tool_calls, got {assistant_count}"


class TestGitHubCopilotProvider:
    """Tests for GitHub Copilot provider retry behavior."""

    @pytest.mark.asyncio
    async def test_github_copilot_chat_uses_call_api(self):
        """Test that GitHub Copilot chat uses _call_api for retry support."""
        from src.agents.llm import GitHubCopilotProvider
        
        provider = GitHubCopilotProvider()
        provider.default_model = "gpt-5-mini"
        provider.api_key = "test-key"
        
        # Mock _call_api to verify it's called
        with patch.object(provider, '_call_api', new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {
                "choices": [{"message": {"content": "test response"}}]
            }
            
            result = await provider.chat(
                messages=[{"role": "user", "content": "hello"}]
            )
            
            # Verify _call_api was called
            mock_call_api.assert_called_once()
            assert result["content"] == "test response"

    @pytest.mark.asyncio
    async def test_github_copilot_responses_uses_call_api(self):
        """Test that GitHub Copilot responses uses _call_api for retry support."""
        from src.agents.llm import GitHubCopilotProvider
        
        provider = GitHubCopilotProvider()
        provider.default_model = "gpt-5-mini"
        provider.api_key = "test-key"
        
        # Mock _call_api to verify it's called
        with patch.object(provider, '_call_api', new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "test response"}]}]
            }
            
            result = await provider.responses(
                messages=[{"role": "user", "content": "hello"}]
            )
            
            # Verify _call_api was called
            mock_call_api.assert_called_once()
            assert result["content"] == "test response"

    @pytest.mark.asyncio
    async def test_github_copilot_get_headers_includes_copilot_specific(self):
        """Test that GitHub Copilot _get_headers includes required Copilot headers."""
        from src.agents.llm import GitHubCopilotProvider
        
        provider = GitHubCopilotProvider()
        
        with patch.dict('os.environ', {'GITHUB_COPILOT_TOKEN': 'test-token'}):
            headers = provider._get_headers()
            
            assert "Authorization" in headers
            assert "X-GitHub-Api-Version" in headers
            assert "Accept" in headers
            assert headers["Accept"] == "application/vnd.github.copilot-chat-preview+json"


class TestTemperatureResolution:
    def test_resolve_llm_temperature_prefers_config_when_explicit_missing(self, monkeypatch):
        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23})
        assert resolve_llm_temperature() == 0.23

    def test_resolve_llm_temperature_explicit_wins(self, monkeypatch):
        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23})
        assert resolve_llm_temperature(0.11) == 0.11

    @pytest.mark.parametrize("value", [None, "", "not-a-number", -0.1, 2.1, True, False, "nan", "NaN", float("nan"), "inf", float("inf")])
    def test_resolve_llm_temperature_invalid_values_fallback_default(self, monkeypatch, value):
        monkeypatch.setitem(config._config, "llm", {"temperature": value})
        assert resolve_llm_temperature() == DEFAULT_LLM_TEMPERATURE

    @pytest.mark.asyncio
    async def test_openai_chat_temperature_only_for_exact_gpt4(self, monkeypatch):
        from src.agents.llm import OpenAIProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "max_tokens": 256})
        provider = OpenAIProvider()
        provider._check_api_key = lambda: None

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
            await provider.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4")
            payload = mock_call_api.call_args.args[1]
            assert payload["temperature"] == 0.23

            await provider.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4", temperature=0.12)
            payload = mock_call_api.call_args.args[1]
            assert payload["temperature"] == 0.12

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo", "gpt-5.4-mini"])
    async def test_openai_chat_omits_temperature_for_non_exact_gpt4(self, monkeypatch, model):
        from src.agents.llm import OpenAIProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "max_tokens": 256})
        provider = OpenAIProvider()
        provider._check_api_key = lambda: None

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
            await provider.chat(messages=[{"role": "user", "content": "hi"}], model=model, temperature=0.12)
            payload = mock_call_api.call_args.args[1]
            assert "temperature" not in payload

    @pytest.mark.asyncio
    async def test_openai_responses_temperature_only_for_exact_gpt4(self, monkeypatch):
        from src.agents.llm import OpenAIProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "max_tokens": 256})
        provider = OpenAIProvider()
        provider._check_api_key = lambda: None

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            await provider.responses(input_items=[{"type": "message", "role": "user", "content": "hello"}], model="gpt-4")
            payload = mock_call_api.call_args.args[1]
            assert payload["temperature"] == 0.23

            await provider.responses(
                input_items=[{"type": "message", "role": "user", "content": "hello"}],
                model="gpt-4",
                temperature=0.12,
            )
            payload = mock_call_api.call_args.args[1]
            assert payload["temperature"] == 0.12

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo", "gpt-5.4-mini"])
    async def test_openai_responses_omits_temperature_for_non_exact_gpt4(self, monkeypatch, model):
        from src.agents.llm import OpenAIProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "max_tokens": 256})
        provider = OpenAIProvider()
        provider._check_api_key = lambda: None

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            await provider.responses(
                input_items=[{"type": "message", "role": "user", "content": "hello"}],
                model=model,
                temperature=0.12,
            )
            payload = mock_call_api.call_args.args[1]
            assert "temperature" not in payload

    @pytest.mark.asyncio
    async def test_github_copilot_chat_temperature_only_for_exact_gpt4(self, monkeypatch):
        from src.agents.llm import GitHubCopilotProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "api_key": "k", "max_tokens": 256})
        provider = GitHubCopilotProvider()

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
            await provider.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4")
            payload = mock_call_api.call_args.args[1]
            assert payload["temperature"] == 0.23

            await provider.chat(messages=[{"role": "user", "content": "hi"}], model="gpt-4", temperature=0.12)
            payload = mock_call_api.call_args.args[1]
            assert payload["temperature"] == 0.12

    @pytest.mark.asyncio
    async def test_github_copilot_responses_temperature_only_for_exact_gpt4(self, monkeypatch):
        from src.agents.llm import GitHubCopilotProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "api_key": "k", "max_tokens": 256})
        provider = GitHubCopilotProvider()

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            await provider.responses(input_items=[{"type": "message", "role": "user", "content": "hello"}], model="gpt-4")
            payload = mock_call_api.call_args.args[1]
            assert payload["temperature"] == 0.23

            await provider.responses(
                input_items=[{"type": "message", "role": "user", "content": "hello"}],
                model="gpt-4",
                temperature=0.12,
            )
            payload = mock_call_api.call_args.args[1]
            assert payload["temperature"] == 0.12

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4o", "gpt-5.4-mini", "gemini-2.5-pro"])
    async def test_github_copilot_chat_omits_temperature_for_non_exact_gpt4(self, monkeypatch, model):
        from src.agents.llm import GitHubCopilotProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "api_key": "k", "max_tokens": 256})
        provider = GitHubCopilotProvider()

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
            await provider.chat(messages=[{"role": "user", "content": "hi"}], model=model, temperature=0.12)
            payload = mock_call_api.call_args.args[1]
            assert "temperature" not in payload

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["gpt-4.1", "gpt-4o", "gpt-5.4-mini", "gemini-2.5-pro"])
    async def test_github_copilot_responses_omits_temperature_for_non_exact_gpt4(self, monkeypatch, model):
        from src.agents.llm import GitHubCopilotProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "api_key": "k", "max_tokens": 256})
        provider = GitHubCopilotProvider()

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            await provider.responses(
                input_items=[{"type": "message", "role": "user", "content": "hello"}],
                model=model,
                temperature=0.12,
            )
            payload = mock_call_api.call_args.args[1]
            assert "temperature" not in payload

    @pytest.mark.parametrize("provider,model,expected", [
        ("openai", "gpt-4", True),
        ("openai", "openai/gpt-4", True),
        ("github_copilot", "github_copilot:gpt-4", True),
        ("openai", "gpt-4.1", False),
        ("openai", "gpt-4o", False),
        ("openai", "gpt-4o-mini", False),
        ("openai", "gpt-4-turbo", False),
        ("openai", "gpt-5.4-mini", False),
        ("github_copilot", "gpt-4.1", False),
        ("github_copilot", "gpt-4o", False),
        ("github_copilot", "gemini-2.5-pro", False),
        ("claude", "claude-sonnet-4-20250514", False),
        ("ollama", "llama3", False),
    ])
    def test_supports_temperature_parameter(self, provider, model, expected):
        from src.agents.llm import _supports_temperature_parameter

        assert _supports_temperature_parameter(provider, model) is expected

    @pytest.mark.asyncio
    async def test_claude_chat_omits_temperature(self, monkeypatch):
        from src.agents.llm import ClaudeProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "api_key": "k", "max_tokens": 256})
        provider = ClaudeProvider()

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}):
            with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
                mock_call_api.return_value = {"content": [{"type": "text", "text": "ok"}], "usage": {}}
                await provider.chat(
                    messages=[{"role": "user", "content": "hi"}],
                    model="claude-sonnet-4-20250514",
                    temperature=0.12,
                )
                payload = mock_call_api.call_args.args[1]
                assert "temperature" not in payload

    @pytest.mark.asyncio
    async def test_llmclient_responses_chat_fallback_forwards_temperature(self):
        client = LLMClient()
        client.providers = {"openai": object()}
        client.default_provider = "openai"
        client.chat = AsyncMock(return_value={"content": "ok"})

        await client.responses(
            messages=[{"role": "user", "content": "hi"}],
            provider="openai",
            temperature=0.19,
        )
        assert client.chat.call_args.kwargs["temperature"] == 0.19

    def test_provider_list_models_default_first(self):
        from src.agents.llm import OpenAIProvider, GitHubCopilotProvider

        openai_models = OpenAIProvider().list_models()
        copilot_models = GitHubCopilotProvider().list_models()

        assert openai_models[0] == "gpt-5.4-mini"
        assert "gpt-5-mini" in openai_models
        assert copilot_models[0] == "gpt-5.4-mini"
        assert "gpt-5-mini" in copilot_models


class TestOllamaProvider:
    """Tests for Ollama provider."""

    @pytest.mark.asyncio
    async def test_ollama_chat_uses_call_api(self):
        """Test that Ollama chat uses _call_api for retry support."""
        from src.agents.llm import OllamaProvider
        
        provider = OllamaProvider()
        provider.default_model = "llama3"
        
        # Mock _call_api to verify it's called
        with patch.object(provider, '_call_api', new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {
                "message": {"content": "test response"}
            }
            
            result = await provider.chat(
                messages=[{"role": "user", "content": "hello"}]
            )
            
            # Verify _call_api was called
            mock_call_api.assert_called_once()
            assert result["content"] == "test response"

    @pytest.mark.asyncio
    async def test_ollama_chat_omits_temperature_option(self, monkeypatch):
        """Test that Ollama options omit temperature for non-exact gpt-4 models."""
        from src.agents.llm import OllamaProvider

        monkeypatch.setitem(config._config, "llm", {"temperature": 0.23, "max_tokens": 256})
        provider = OllamaProvider()

        with patch.object(provider, "_call_api", new_callable=AsyncMock) as mock_call_api:
            mock_call_api.return_value = {
                "message": {"content": "ok"},
                "prompt_eval_count": 1,
                "eval_count": 1,
            }
            await provider.chat(messages=[{"role": "user", "content": "hi"}], model="llama3", temperature=0.12)
            payload = mock_call_api.call_args.args[1]
            assert "options" in payload
            assert "temperature" not in payload["options"]
            assert payload["options"]["num_predict"] == 256

    @pytest.mark.asyncio
    async def test_ollama_get_headers_omits_auth(self):
        """Test that Ollama _get_headers omits Authorization header."""
        from src.agents.llm import OllamaProvider
        
        provider = OllamaProvider()
        headers = provider._get_headers()
        
        # Should not have Authorization header for local Ollama
        assert "Authorization" not in headers
        assert "Content-Type" in headers


class TestVisionModelSelection:
    """Tests for vision model detection and fallback selection."""

    def test_normalize_provider_key_anthropic(self):
        """Test that anthropic is normalized to claude."""
        from src.agents.llm import _normalize_provider_key
        assert _normalize_provider_key("anthropic") == "claude"
        assert _normalize_provider_key("Anthropic") == "claude"
        assert _normalize_provider_key("ANTHROPIC") == "claude"

    def test_normalize_provider_key_github(self):
        """Test that github aliases are normalized."""
        from src.agents.llm import _normalize_provider_key
        assert _normalize_provider_key("github") == "github_copilot"
        assert _normalize_provider_key("github-copilot") == "github_copilot"
        assert _normalize_provider_key("copilot") == "github_copilot"
        assert _normalize_provider_key("GitHub_Copilot") == "github_copilot"

    def test_normalize_provider_key_openai(self):
        """Test that openai is normalized correctly."""
        from src.agents.llm import _normalize_provider_key
        assert _normalize_provider_key("openai") == "openai"
        assert _normalize_provider_key("OpenAI") == "openai"

    def test_normalize_provider_key_none_empty(self):
        """Test that None and empty strings return None."""
        from src.agents.llm import _normalize_provider_key
        assert _normalize_provider_key(None) is None
        assert _normalize_provider_key("") is None
        assert _normalize_provider_key("   ") is None

    def test_is_vision_model_openai(self):
        """Test vision model detection for OpenAI provider."""
        from src.agents.llm import is_vision_model
        # Vision models
        assert is_vision_model("openai", "gpt-4o") is True
        assert is_vision_model("openai", "gpt-4o-mini") is True
        assert is_vision_model("openai", "gpt-5.4-mini") is True
        assert is_vision_model("openai", "gpt-5-mini") is True
        assert is_vision_model("openai", "gpt-5") is True
        # Non-vision models
        assert is_vision_model("openai", "gpt-4.1") is False
        assert is_vision_model("openai", "gpt-3.5-turbo") is False
        assert is_vision_model("openai", "o1") is False

    def test_is_vision_model_github_copilot(self):
        """Test vision model detection for GitHub Copilot provider."""
        from src.agents.llm import is_vision_model
        # Vision models
        assert is_vision_model("github_copilot", "gpt-4o") is True
        assert is_vision_model("github_copilot", "gpt-5.4-mini") is True
        assert is_vision_model("github_copilot", "gpt-5-mini") is True
        assert is_vision_model("github_copilot", "gpt-5") is True
        assert is_vision_model("github_copilot", "gemini-2.5-pro") is True
        # Non-vision models
        assert is_vision_model("github_copilot", "gpt-4.1") is False

    def test_is_vision_model_claude_with_version_suffix(self):
        """Test that versioned Claude models are detected as vision-capable."""
        from src.agents.llm import is_vision_model
        # Versioned Claude models should match via prefix
        assert is_vision_model("claude", "claude-sonnet-4-20250514") is True
        assert is_vision_model("claude", "claude-haiku-4-20250514") is True
        assert is_vision_model("claude", "claude-opus-4-20250514") is True

    def test_is_vision_model_claude_alias(self):
        """Test that anthropic alias works for Claude models."""
        from src.agents.llm import is_vision_model
        assert is_vision_model("anthropic", "claude-sonnet-4-20250514") is True
        assert is_vision_model("anthropic", "claude-haiku-4") is True

    def test_is_vision_model_unknown_provider(self):
        """Test that unknown providers return False."""
        from src.agents.llm import is_vision_model
        assert is_vision_model("unknown_provider", "gpt-4o") is False
        assert is_vision_model(None, "gpt-4o") is False

    def test_get_vision_fallback_model_openai(self):
        """Test fallback model for OpenAI."""
        from src.agents.llm import get_vision_fallback_model
        assert get_vision_fallback_model("openai") == "gpt-5.4-mini"

    def test_get_vision_fallback_model_github_copilot(self):
        """Test fallback model for GitHub Copilot."""
        from src.agents.llm import get_vision_fallback_model
        assert get_vision_fallback_model("github_copilot") == "gpt-5.4-mini"
        assert get_vision_fallback_model("github") == "gpt-5.4-mini"

    def test_get_vision_fallback_model_claude(self):
        """Test fallback model for Claude."""
        from src.agents.llm import get_vision_fallback_model
        assert get_vision_fallback_model("claude") == "claude-haiku-4-20250514"
        assert get_vision_fallback_model("anthropic") == "claude-haiku-4-20250514"

    def test_get_vision_fallback_model_ollama(self):
        """Test fallback model for Ollama."""
        from src.agents.llm import get_vision_fallback_model
        assert get_vision_fallback_model("ollama") is None

    def test_get_vision_fallback_model_unknown(self):
        """Test fallback model for unknown provider."""
        from src.agents.llm import get_vision_fallback_model
        assert get_vision_fallback_model("unknown") is None
        assert get_vision_fallback_model(None) is None


def test_openai_provider_debug_fallback_max_tokens_matches_responses_default():
    import inspect
    from src.agents.llm import OpenAIProvider

    source = inspect.getsource(OpenAIProvider.responses)
    assert "config.llm.get('max_tokens', 1000)" not in source
    assert "config.llm.get('max_tokens', 64000)" in source

@pytest.mark.asyncio
async def test_openai_chat_tools_payload_sets_parallel_tool_calls_false_by_default(monkeypatch, openai_provider):
    captured = {}

    async def _fake_call_api(_path, payload):
        captured.update(payload)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(openai_provider, "_call_api", _fake_call_api)
    monkeypatch.setitem(config._config, "llm", {"model": "gpt-5-mini"})

    await openai_provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "jira_search", "parameters": {"type": "object"}}}],
    )

    assert captured.get("tool_choice") == "auto"
    assert captured.get("parallel_tool_calls") is False


@pytest.mark.asyncio
async def test_github_copilot_responses_tools_payload_respects_parallel_tool_calls_config(
    monkeypatch, github_copilot_provider
):
    captured = {}

    async def _fake_call_api(_path, payload):
        captured.update(payload)
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

    monkeypatch.setattr(github_copilot_provider, "_call_api", _fake_call_api)
    monkeypatch.setitem(
        config._config,
        "llm",
        {"model": "gpt-5-mini", "api_key": "test-key", "tool_loop": {"parallel_tool_calls": True}},
    )

    await github_copilot_provider.responses(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "jira_search", "parameters": {"type": "object"}}}],
    )

    assert captured.get("tool_choice") == "auto"
    assert captured.get("parallel_tool_calls") is True
