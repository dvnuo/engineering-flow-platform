"""Tests for Heartbeat module."""

import pytest


class TestHeartbeatChecker:
    """Tests for HeartbeatChecker class."""

    def test_heartbeat_initialization(self):
        """Test HeartbeatChecker initialization."""
        from src.agents.heartbeat import HeartbeatChecker

        checker = HeartbeatChecker(check_interval=300)

        assert checker.check_interval == 300
        assert checker.running is False
        assert checker._last_checks == {}

    def test_get_effective_interval(self):
        """Test interval calculation uses the base interval."""
        from src.agents.heartbeat import HeartbeatChecker

        checker = HeartbeatChecker(check_interval=300)

        assert checker._get_effective_interval() == 300

    def test_get_check_detail_level(self):
        """Test detail level is a constant value."""
        from src.agents.heartbeat import HeartbeatChecker

        checker = HeartbeatChecker()

        assert checker._get_check_detail_level() == "normal"

    @pytest.mark.asyncio
    async def test_check_emails(self):
        """Test email check output."""
        from src.agents.heartbeat import HeartbeatChecker

        checker = HeartbeatChecker()
        result = await checker._check_emails()

        assert result["type"] == "emails"
        assert "unread_count" in result
        assert "important_count" in result
        assert "action_required" in result

    @pytest.mark.asyncio
    async def test_check_calendar(self):
        """Test calendar check output."""
        from src.agents.heartbeat import HeartbeatChecker

        checker = HeartbeatChecker()
        result = await checker._check_calendar()

        assert result["type"] == "calendar"
        assert "today_count" in result
        assert "conflicts" in result
        assert "upcoming_important" in result

    @pytest.mark.asyncio
    async def test_check_weather(self):
        """Test weather check output."""
        from src.agents.heartbeat import HeartbeatChecker

        checker = HeartbeatChecker()
        result = await checker._check_weather()

        assert result["type"] == "weather"
        assert "forecast" in result
        assert "alerts" in result

    @pytest.mark.asyncio
    async def test_run_checks(self):
        """Test running all heartbeat checks."""
        from src.agents.heartbeat import HeartbeatChecker

        checker = HeartbeatChecker()
        results = await checker.run_checks()

        assert "timestamp" in results
        assert "detail_level" in results
        assert "checks" in results
        assert "emails" in results["checks"]
        assert "calendar" in results["checks"]
        assert "weather" in results["checks"]


class TestHeartbeatGlobalFunctions:
    """Tests for global heartbeat functions."""

    def test_get_heartbeat(self):
        """Test getting global heartbeat instance."""
        from src.agents.heartbeat import get_heartbeat
        import src.agents.heartbeat as heartbeat_mod

        # Reset global
        heartbeat_mod._heartbeat = None

        heartbeat = get_heartbeat()

        assert heartbeat is not None
        assert isinstance(heartbeat, heartbeat_mod.HeartbeatChecker)

        # Reset
        heartbeat_mod._heartbeat = None

    def test_start_stop_heartbeat(self):
        """Test starting and stopping heartbeat."""
        import src.agents.heartbeat as heartbeat_mod
        heartbeat_mod._heartbeat = None

        from src.agents.heartbeat import start_heartbeat, stop_heartbeat

        # These should not raise
        import asyncio
        asyncio.run(start_heartbeat())
        asyncio.run(stop_heartbeat())
