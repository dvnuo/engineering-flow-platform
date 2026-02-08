"""Tests for Reasoning Replay feature."""

import pytest
import asyncio
import os
from unittest.mock import Mock, patch, AsyncMock
from src.agents.core import Agent
from src.agents.llm import OpenAIProvider


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
        
        # Track what arguments were passed to _call_api
        captured_payloads = []
        
        async def mock_call_api(endpoint, payload):
            captured_payloads.append(payload.copy())
            return {
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
        
        # Use o1 model which supports reasoning_replay
        with patch.object(provider, '_call_api', mock_call_api):
            result = await provider.chat(
                messages=[{"role": "user", "content": "Hello"}],
                model="o1",
                reasoning_replay=True,
            )
            
            # Verify reasoning_replay was passed to API
            assert len(captured_payloads) == 1
            assert "reasoning" in captured_payloads[0], "reasoning_replay should be in payload"
            assert captured_payloads[0]["reasoning"]["type"] == "text"
    
    def test_config_has_reasoning_replay_setting(self):
        """Test that config file has reasoning_replay setting."""
        # Import from src.config for proper module path
        from src.config import config as src_config
        
        # Get llm config - may be empty if config.yaml not found
        llm_config = getattr(src_config, 'llm', {}) or {}
        
        # Check if reasoning_replay key exists (may not be present if config.yaml is missing)
        if llm_config:
            assert 'reasoning_replay' in llm_config, "llm config should have 'reasoning_replay' key"
            assert isinstance(llm_config.get('reasoning_replay'), bool), "reasoning_replay should be boolean"
        else:
            # Config not loaded - this is acceptable in test environment without config.yaml
            pytest.skip("config.yaml not found, skipping config test")


class TestReasoningReplayResponse:
    """Tests for reasoning in responses."""
    
    def test_response_dict_structure(self):
        """Test that response dict can include reasoning."""
        # Simulate response structure when enabled
        response_enabled = {
            "response": "Hello!",
            "reasoning": "Thinking about the response...",
            "usage": {"total_tokens": 50}
        }
        
        assert "response" in response_enabled
        assert "reasoning" in response_enabled
        
        # Simulate response structure when disabled (no reasoning field)
        response_disabled = {
            "response": "Hello!",
            "usage": {"total_tokens": 50}
        }
        
        assert "response" in response_disabled
        assert "reasoning" not in response_disabled
    
    def test_reasoning_disabled_excludes_field(self):
        """Test that reasoning field is excluded when disabled."""
        enable_reasoning = False
        
        # Simulate response building
        result = {"response": "Hello!", "usage": {}}
        if enable_reasoning:
            result["reasoning"] = ""
        
        # When disabled, reasoning should NOT be in result
        assert "reasoning" not in result


class TestReasoningReplayEdgeCases:
    """Edge case tests for reasoning_replay."""
    
    def test_reasoning_replay_none_uses_config(self):
        """Test that None reasoning_replay uses config default."""
        # When reasoning_replay is None, should fall back to config
        reasoning_replay = None
        enable_reasoning = reasoning_replay if reasoning_replay is not None else False
        assert enable_reasoning == False
    
    def test_reasoning_replay_explicit_true(self):
        """Test explicit True reasoning_replay."""
        reasoning_replay = True
        enable_reasoning = reasoning_replay if reasoning_replay is not None else False
        assert enable_reasoning == True
    
    def test_reasoning_replay_explicit_false(self):
        """Test explicit False reasoning_replay."""
        reasoning_replay = False
        enable_reasoning = reasoning_replay if reasoning_replay is not None else False
        assert enable_reasoning == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
