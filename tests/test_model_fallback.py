"""Tests for Model Fallback functionality."""

import pytest
import asyncio
from typing import Any
from pathlib import Path

from src.agents.model_fallback import (
    ModelCandidate,
    FallbackAttempt,
    FallbackError,
    with_model_fallback,
    classify_fallback_error,
    should_skip_fallback,
    FALLBACK_ORDER,
    FAST_FALLBACK,
    BUDGET_FALLBACK,
    LOCAL_FALLBACK,
    get_fallback_order,
)
from src.config import DEFAULT_LLM_MODEL


class TestModelCandidate:
    """Tests for ModelCandidate class."""
    
    def test_init(self):
        """Test ModelCandidate initialization."""
        candidate = ModelCandidate(
            provider="openai",
            model="gpt-4o",
            priority=0,
            weight=1.0
        )
        
        assert candidate.provider == "openai"
        assert candidate.model == "gpt-4o"
        assert candidate.priority == 0
        assert candidate.weight == 1.0
    
    def test_repr(self):
        """Test ModelCandidate string representation."""
        candidate = ModelCandidate(provider="openai", model="gpt-4o")
        assert repr(candidate) == "openai/gpt-4o"
    
    def test_to_dict(self):
        """Test ModelCandidate to_dict()."""
        candidate = ModelCandidate(
            provider="anthropic",
            model="claude-sonnet-4",
            priority=1,
            weight=0.8
        )
        
        result = candidate.to_dict()
        assert result == {
            "provider": "anthropic",
            "model": "claude-sonnet-4",
            "priority": 1,
            "weight": 0.8,
        }


class TestClassifyFallbackError:
    """Tests for error classification."""
    
    def test_authentication_error(self):
        """Test that auth errors skip fallback."""
        error = Exception("Authentication failed: invalid API key")
        result = classify_fallback_error(error)
        assert result == "skip"
    
    def test_rate_limit_error(self):
        """Test that rate limit errors skip fallback."""
        error = Exception("Rate limit exceeded")
        result = classify_fallback_error(error)
        assert result == "skip"
    
    def test_quota_error(self):
        """Test that quota errors skip fallback."""
        error = Exception("Quota exceeded")
        result = classify_fallback_error(error)
        assert result == "skip"
    
    def test_context_length_error(self):
        """Test that context length errors skip fallback."""
        error = Exception("Context length exceeded")
        result = classify_fallback_error(error)
        assert result == "skip"
    
    @pytest.mark.asyncio
    async def test_timeout_error(self):
        """Test that timeout errors trigger fallback."""
        error = asyncio.TimeoutError("Request timed out")
        result = classify_fallback_error(error)
        assert result == "fallback"
    
    def test_connection_error(self):
        """Test that connection errors trigger fallback."""
        error = Exception("Connection refused")
        result = classify_fallback_error(error)
        assert result == "fallback"
    
    def test_server_error(self):
        """Test that server errors trigger fallback."""
        error = Exception("Internal server error")
        result = classify_fallback_error(error)
        assert result == "fallback"
    
    def test_unknown_error(self):
        """Test that unknown errors skip fallback."""
        error = Exception("Some unexpected error")
        result = classify_fallback_error(error)
        assert result == "skip"


class TestShouldSkipFallback:
    """Tests for should_skip_fallback()."""
    
    def test_skip(self):
        """Test skip return True."""
        assert should_skip_fallback("skip") is True
    
    def test_fallback(self):
        """Test fallback return False."""
        assert should_skip_fallback("fallback") is False


class TestWithModelFallback:
    """Tests for with_model_fallback()."""
    
    @pytest.mark.asyncio
    async def test_success_first_candidate(self):
        """Test successful execution on first candidate."""
        call_count = 0
        
        async def task():
            nonlocal call_count
            call_count += 1
            return "success"
        
        candidates = [
            ModelCandidate(provider="openai", model="gpt-4o"),
        ]
        
        result = await with_model_fallback(task, candidates)
        
        assert result == "success"
        assert call_count == 1
    
    @pytest.mark.asyncio
    async def test_fallback_to_second(self):
        """Test fallback to second candidate."""
        call_count = 0
        
        async def task():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Connection refused")
            return "success"
        
        candidates = [
            ModelCandidate(provider="openai", model="gpt-4o"),
            ModelCandidate(provider="anthropic", model="claude-sonnet-4"),
        ]
        
        result = await with_model_fallback(task, candidates)
        
        assert result == "success"
        assert call_count == 2
    
    @pytest.mark.asyncio
    async def test_all_candidates_fail(self):
        """Test error when all candidates fail."""
        async def task():
            raise Exception("Connection refused")
        
        candidates = [
            ModelCandidate(provider="openai", model="gpt-4o"),
            ModelCandidate(provider="anthropic", model="claude-sonnet-4"),
        ]
        
        with pytest.raises(FallbackError) as exc_info:
            await with_model_fallback(task, candidates)
        
        assert "All 2 models failed" in str(exc_info.value)
        assert len(exc_info.value.attempts) == 2
    
    @pytest.mark.asyncio
    async def test_auth_error_skips_immediately(self):
        """Test that auth errors are recorded but don't prevent trying next."""
        call_order = []
        
        async def task():
            # Simulate: first candidate auth fails, second succeeds
            if not call_order:
                call_order.append(1)
                raise Exception("Invalid API key for openai")
            else:
                call_order.append(2)
                return "success from anthropic"
        
        candidates = [
            ModelCandidate(provider="openai", model="gpt-4o"),
            ModelCandidate(provider="anthropic", model="claude-sonnet-4"),
        ]
        
        result = await with_model_fallback(task, candidates)
        
        assert result == "success from anthropic"
        assert len(call_order) == 2
    
    @pytest.mark.asyncio
    async def test_empty_candidates(self):
        """Test with empty candidates list."""
        async def task():
            return "success"
        
        result = await with_model_fallback(task, [])
        
        assert result == "success"


class TestPredefinedOrders:
    """Tests for predefined fallback orders."""
    
    def test_fallback_order_not_empty(self):
        """Test FALLBACK_ORDER is not empty."""
        assert len(FALLBACK_ORDER) > 0
    
    def test_fast_fallback_not_empty(self):
        """Test FAST_FALLBACK is not empty."""
        assert len(FAST_FALLBACK) > 0
    
    def test_budget_fallback_not_empty(self):
        """Test BUDGET_FALLBACK is not empty."""
        assert len(BUDGET_FALLBACK) > 0
    
    def test_local_fallback_not_empty(self):
        """Test LOCAL_FALLBACK is not empty."""
        assert len(LOCAL_FALLBACK) > 0
    
    def test_get_fallback_order_default(self):
        """Test get_fallback_order with default."""
        result = get_fallback_order("default")
        assert result == FALLBACK_ORDER
    
    def test_get_fallback_order_fast(self):
        """Test get_fallback_order with fast."""
        result = get_fallback_order("fast")
        assert result == FAST_FALLBACK
    
    def test_get_fallback_order_unknown(self):
        """Test get_fallback_order with unknown returns default."""
        result = get_fallback_order("unknown")
        assert result == FALLBACK_ORDER

    def test_default_fallback_order_prefers_default_llm_model(self):
        assert FALLBACK_ORDER[0].provider == "openai"
        assert FALLBACK_ORDER[0].model == DEFAULT_LLM_MODEL
        assert FALLBACK_ORDER[0].priority == 0

    def test_fast_fallback_order_prefers_default_llm_model(self):
        assert FAST_FALLBACK[0].provider == "openai"
        assert FAST_FALLBACK[0].model == DEFAULT_LLM_MODEL
        assert FAST_FALLBACK[0].priority == 0

    def test_unknown_fallback_order_uses_default_llm_model_first(self):
        result = get_fallback_order("unknown")
        assert result[0].model == DEFAULT_LLM_MODEL
    
    def test_fallback_order_priority(self):
        """Test that FALLBACK_ORDER is ordered by priority."""
        priorities = [c.priority for c in FALLBACK_ORDER]
        assert priorities == sorted(priorities)
    
    def test_fast_fallback_shorter(self):
        """Test that FAST_FALLBACK is shorter than FALLBACK_ORDER."""
        assert len(FAST_FALLBACK) < len(FALLBACK_ORDER)


class TestFallbackAttempt:
    """Tests for FallbackAttempt class."""
    
    def test_to_dict(self):
        """Test FallbackAttempt to_dict()."""
        candidate = ModelCandidate(provider="openai", model="gpt-4o")
        attempt = FallbackAttempt(
            candidate=candidate,
            error="Connection refused",
            reason="fallback",
            success=False,
            duration_ms=1500.0
        )
        
        result = attempt.to_dict()
        assert result == {
            "provider": "openai",
            "model": "gpt-4o",
            "error": "Connection refused",
            "reason": "fallback",
            "success": False,
            "duration_ms": 1500.0,
        }

    def test_successful_attempt(self):
        """Test successful attempt."""
        candidate = ModelCandidate(provider="openai", model="gpt-4o")
        attempt = FallbackAttempt(
            candidate=candidate,
            success=True,
            duration_ms=500.0
        )

        assert attempt.error is None
        assert attempt.success is True


def test_readme_llm_example_uses_default_llm_model():
    text = Path("README.md").read_text(encoding="utf-8")
    llm_section = text[text.find("### LLM Providers"): text.find("### Control-Plane Runtime Settings")]
    assert 'model: "gpt-5.4"' in llm_section
    assert 'model: "gpt-4o"' not in llm_section


class TestIntegration:
    """Integration tests for model fallback."""
    
    @pytest.mark.asyncio
    async def test_multi_model_fallback(self):
        """Test fallback through multiple models."""
        results = []
        
        async def task(provider: str, model: str) -> str:
            results.append(f"{provider}/{model}")
            if len(results) < 3:
                raise Exception("Connection refused")
            return f"Response from {provider}/{model}"
        
        candidates = [
            ModelCandidate(provider="openai", model="gpt-4o"),
            ModelCandidate(provider="openai", model="gpt-4o-mini"),
            ModelCandidate(provider="anthropic", model="claude-sonnet-4"),
        ]
        
        result = await with_model_fallback(
            task=lambda: task(candidates[0].provider, candidates[0].model),
            candidates=candidates[1:]
        )
        
        assert result == "Response from openai/gpt-4o"
        assert len(results) == 3
