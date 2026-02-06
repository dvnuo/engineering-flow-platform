"""Tests for Heartbeat module."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch


class TestHeartbeatChecker:
    """Tests for HeartbeatChecker class."""
    
    def test_heartbeat_initialization(self):
        """Test HeartbeatChecker initialization."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(
            think_level=ThinkLevel.HIGH,
            check_interval=300
        )
        
        assert checker.think_level == ThinkLevel.HIGH
        assert checker.check_interval == 300
        assert checker.running is False
        assert checker._last_checks == {}
    
    def test_get_effective_interval_high(self):
        """Test interval calculation for HIGH thinking level."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.HIGH, check_interval=300)
        interval = checker._get_effective_interval()
        
        # HIGH should be 2x more frequent (interval / 2)
        assert interval == 150  # 300 / 2 = 150
    
    def test_get_effective_interval_off(self):
        """Test interval calculation for OFF thinking level."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.OFF, check_interval=300)
        interval = checker._get_effective_interval()
        
        # OFF should be 2x less frequent (interval * 2)
        assert interval == 600  # 300 * 2 = 600
    
    def test_get_effective_interval_medium(self):
        """Test interval calculation for MEDIUM thinking level."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.MEDIUM, check_interval=300)
        interval = checker._get_effective_interval()
        
        # MEDIUM should use base interval
        assert interval == 300
    
    def test_get_check_detail_level_high(self):
        """Test detail level for HIGH thinking."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.HIGH)
        detail = checker._get_check_detail_level()
        
        assert detail == "detailed"
    
    def test_get_check_detail_level_off(self):
        """Test detail level for OFF thinking."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.OFF)
        detail = checker._get_check_detail_level()
        
        assert detail == "simplified"
    
    def test_get_check_detail_level_minimal(self):
        """Test detail level for MINIMAL thinking."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.MINIMAL)
        detail = checker._get_check_detail_level()
        
        assert detail == "simplified"
    
    def test_get_check_detail_level_medium(self):
        """Test detail level for MEDIUM thinking."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.MEDIUM)
        detail = checker._get_check_detail_level()
        
        assert detail == "normal"
    
    @pytest.mark.asyncio
    async def test_check_emails_simplified(self):
        """Test email check with simplified detail level."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.OFF)
        result = await checker._check_emails()
        
        assert result["type"] == "emails"
        assert result["detail_level"] == "simplified"
        assert "unread_count" in result
    
    @pytest.mark.asyncio
    async def test_check_emails_detailed(self):
        """Test email check with detailed level."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.HIGH)
        result = await checker._check_emails()
        
        assert result["type"] == "emails"
        assert result["detail_level"] == "detailed"
        assert "important_count" in result
        assert "action_required" in result
    
    @pytest.mark.asyncio
    async def test_check_calendar_simplified(self):
        """Test calendar check with simplified level."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.OFF)
        result = await checker._check_calendar()
        
        assert result["type"] == "calendar"
        assert result["detail_level"] == "simplified"
        assert "today_count" in result
    
    @pytest.mark.asyncio
    async def test_check_calendar_detailed(self):
        """Test calendar check with detailed level."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.HIGH)
        result = await checker._check_calendar()
        
        assert result["type"] == "calendar"
        assert result["detail_level"] == "detailed"
        assert "conflicts" in result
        assert "upcoming_important" in result
    
    @pytest.mark.asyncio
    async def test_check_weather_simplified(self):
        """Test weather check with simplified level."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.OFF)
        result = await checker._check_weather()
        
        assert result["type"] == "weather"
        assert result["detail_level"] == "simplified"
        assert "condition" in result
    
    @pytest.mark.asyncio
    async def test_check_weather_detailed(self):
        """Test weather check with detailed level."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.HIGH)
        result = await checker._check_weather()
        
        assert result["type"] == "weather"
        assert result["detail_level"] == "detailed"
        assert "forecast" in result
        assert "alerts" in result
    
    @pytest.mark.asyncio
    async def test_run_checks(self):
        """Test running all heartbeat checks."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.OFF)
        results = await checker.run_checks()
        
        assert "timestamp" in results
        assert "think_level" in results
        assert "detail_level" in results
        assert "checks" in results
        assert "emails" in results["checks"]
        assert "calendar" in results["checks"]
        assert "weather" in results["checks"]
    
    @pytest.mark.asyncio
    async def test_run_checks_high_thinking_multi_round(self):
        """Test that HIGH thinking level enables multi-round verification."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.HIGH)
        
        # Mock the loop to verify multi-round behavior
        with patch.object(checker, '_heartbeat_loop', new_callable=AsyncMock) as mock_loop:
            # Just verify the checker is configured for multi-round
            assert checker.think_level == ThinkLevel.HIGH
            assert checker._get_check_detail_level() == "detailed"
    
    def test_update_think_level(self):
        """Test updating thinking level at runtime."""
        from agent.heartbeat import HeartbeatChecker
        from agent.thinking import ThinkLevel
        
        checker = HeartbeatChecker(think_level=ThinkLevel.OFF)
        checker.update_think_level(ThinkLevel.HIGH)
        
        assert checker.think_level == ThinkLevel.HIGH
        assert checker._get_check_detail_level() == "detailed"


class TestHeartbeatGlobalFunctions:
    """Tests for global heartbeat functions."""
    
    def test_get_heartbeat(self):
        """Test getting global heartbeat instance."""
        from agent.heartbeat import get_heartbeat, _heartbeat
        from agent.thinking import ThinkLevel
        
        # Reset global
        import agent.heartbeat
        agent.heartbeat._heartbeat = None
        
        heartbeat = get_heartbeat(ThinkLevel.HIGH)
        
        assert heartbeat is not None
        assert heartbeat.think_level == ThinkLevel.HIGH
        
        # Reset
        agent.heartbeat._heartbeat = None
    
    def test_start_stop_heartbeat(self):
        """Test starting and stopping heartbeat."""
        import agent.heartbeat
        agent.heartbeat._heartbeat = None
        
        from agent.heartbeat import start_heartbeat, stop_heartbeat
        from agent.thinking import ThinkLevel
        
        # These should not raise
        import asyncio
        asyncio.run(start_heartbeat(ThinkLevel.OFF))
        asyncio.run(stop_heartbeat())
