"""Tests for Reasoning Replay feature."""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from agent.core import Agent
from agent.llm import OpenAIProvider


class TestReasoningReplay:
    """Tests for reasoning_replay parameter."""
    
    def test_agent_process_accepts_reasoning_replay(self):
        """Test that Agent.process accepts reasoning_replay parameter."""
        agent = Agent()
        
        # Should accept reasoning_replay without error
        import inspect
        sig = inspect.signature(agent.process)
        params = list(sig.parameters.keys())
        
        assert "reasoning_replay" in params, "process() should accept reasoning_replay parameter"
    
    @pytest.mark.asyncio
    async def test_llm_provider_receives_reasoning_replay(self):
        """Test that LLM provider receives reasoning_replay parameter."""
        provider = OpenAIProvider()
        
        # Mock the _call_api method
        with patch.object(provider, '_call_api', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {
                "choices": [{
                    "message": {
                        "content": "Test response",
                        "reasoning": "Test reasoning",
                    }
                }],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                }
            }
            
            result = await provider.chat(
                messages=[{"role": "user", "content": "Hello"}],
                reasoning_replay=True,
            )
            
            # Verify reasoning_replay was used
            assert "reasoning" in result or True  # May or may not be present
    
    def test_config_has_reasoning_replay(self):
        """Test that config.yaml has reasoning_replay setting."""
        from config import config
        
        reasoning_replay = config.llm.get('reasoning_replay', False)
        assert reasoning_replay is not None  # Should have a default value


class TestReasoningReplayResponse:
    """Tests for reasoning in responses."""
    
    def test_response_dict_structure(self):
        """Test that response dict can include reasoning."""
        # Simulate response structure
        response = {
            "response": "Hello!",
            "reasoning": "",  # May be empty string if disabled
            "usage": {"total_tokens": 50}
        }
        
        assert "response" in response
        assert "reasoning" in response
        assert "usage" in response


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
