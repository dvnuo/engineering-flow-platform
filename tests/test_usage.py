"""Tests for usage tracking module."""

import tempfile
import pytest
from pathlib import Path

from session.usage import UsageTracker, UsageStats, estimate_cost


class TestUsageTracker:
    """Tests for UsageTracker class."""
    
    @pytest.fixture
    def tracker(self):
        """Create a temporary usage tracker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield UsageTracker(f"{tmpdir}/usage")
    
    def test_parse_usage_from_response(self, tracker):
        """Test parsing usage from OpenAI-style response."""
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }
        }
        
        stats = tracker._parse_usage_from_response(response, "gpt-4o")
        
        assert stats.input_tokens == 100
        assert stats.output_tokens == 50
        assert stats.total_tokens == 150
        assert stats.model == "gpt-4o"
    
    def test_estimate_cost(self):
        """Test cost estimation."""
        # GPT-4o: $5/M input, $15/M output
        cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
        assert cost == 20.0  # 5 + 15
        
        # Small request
        cost = estimate_cost("gpt-4o", 1000, 500)
        assert cost == pytest.approx(0.0125)  # (1000/1M * 5) + (500/1M * 15)
    
    def test_estimate_cost_default(self):
        """Test default pricing for unknown models."""
        cost = estimate_cost("unknown-model", 1000, 500)
        assert cost > 0  # Uses default pricing
    
    def test_record_usage(self, tracker):
        """Test recording usage for a session."""
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }
        }
        
        stats = tracker.record_usage(
            "test_session",
            response,
            "gpt-4o",
            channel="discord"
        )
        
        assert stats.input_tokens == 100
        assert stats.output_tokens == 50
        assert stats.cost > 0
    
    def test_get_session_usage(self, tracker):
        """Test retrieving usage for a session."""
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }
        }
        
        # Record multiple requests
        for i in range(3):
            tracker.record_usage(
                "session_usage_test",
                response,
                "gpt-4o"
            )
        
        usages = tracker.get_session_usage("session_usage_test")
        assert len(usages) == 3
    
    def test_get_session_summary(self, tracker):
        """Test getting session usage summary."""
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }
        }
        
        for i in range(5):
            tracker.record_usage("summary_test", response, "gpt-4o")
        
        summary = tracker.get_session_summary("summary_test")
        
        assert summary["request_count"] == 5
        assert summary["total_tokens"] == 750  # 150 * 5
        assert summary["model"] == "gpt-4o"
    
    def test_get_global_summary(self, tracker):
        """Test getting global usage summary."""
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }
        }
        
        # Record from different sessions
        for i in range(3):
            tracker.record_usage(f"global_{i}", response, "gpt-4o")
        
        summary = tracker.get_global_summary()
        
        assert summary["request_count"] == 3
        assert summary["total_tokens"] == 450
    
    def test_get_usage_by_model(self, tracker):
        """Test getting usage breakdown by model."""
        responses = {
            "gpt-4o": {"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            "gpt-3.5-turbo": {"usage": {"prompt_tokens": 200, "completion_tokens": 100}},
        }
        
        for model, resp in responses.items():
            for _ in range(2):
                tracker.record_usage(f"model_{model}", resp, model)
        
        by_model = tracker.get_usage_by_model()
        
        assert "gpt-4o" in by_model
        assert "gpt-3.5-turbo" in by_model
        assert by_model["gpt-4o"]["requests"] == 2
        assert by_model["gpt-3.5-turbo"]["requests"] == 2
    
    def test_clear_session_usage(self, tracker):
        """Test clearing usage for a session."""
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            }
        }
        
        for i in range(3):
            tracker.record_usage(f"clear_{i}", response, "gpt-4o")
        
        # Clear one session
        count = tracker.clear_session_usage("clear_1")
        assert count == 1
        
        # Verify it's gone
        usages = tracker.get_session_usage("clear_1")
        assert len(usages) == 0
    
    def test_usage_stats_to_dict(self):
        """Test UsageStats serialization."""
        stats = UsageStats(
            input_tokens=100,
            output_tokens=50,
            model="gpt-4o",
            cost=0.001
        )
        
        data = stats.to_dict()
        assert data["input_tokens"] == 100
        assert data["output_tokens"] == 50
        assert data["model"] == "gpt-4o"
    
    def test_usage_stats_from_dict(self):
        """Test UsageStats deserialization."""
        data = {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "context_tokens": 80,
            "model": "gpt-4o",
            "cost": 0.001,
            "timestamp": "2026-02-02T12:00:00",
        }
        
        stats = UsageStats.from_dict(data)
        assert stats.input_tokens == 100
        assert stats.output_tokens == 50
        assert stats.model == "gpt-4o"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
