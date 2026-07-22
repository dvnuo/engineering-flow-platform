"""Tests for Fast Lane Commands module."""

import pytest
from unittest.mock import MagicMock


class TestFastLaneCommands:
    """Tests for FastLaneCommands class."""

    def test_is_command_with_slash(self):
        """Test is_command returns True for commands."""
        from src.agents.fastlane import FastLaneCommands

        commands = FastLaneCommands()

        assert commands.is_command("/status") is True
        assert commands.is_command("/help") is True

    def test_is_command_unknown_command(self):
        """Test is_command returns False for removed/unknown commands."""
        from src.agents.fastlane import FastLaneCommands

        commands = FastLaneCommands()

        assert commands.is_command("/thinking high") is False
        assert commands.is_command("/reasoning on") is False

    def test_is_command_without_slash(self):
        """Test is_command returns False for non-commands."""
        from src.agents.fastlane import FastLaneCommands

        commands = FastLaneCommands()

        assert commands.is_command("hello") is False
        assert commands.is_command("what is the weather") is False
        assert commands.is_command("") is False

    def test_parse_command_status(self):
        """Test parsing /status command."""
        from src.agents.fastlane import FastLaneCommands

        commands = FastLaneCommands()

        cmd, arg, full_arg = commands.parse_command("/status")

        assert cmd == "status"
        assert arg is None
        assert full_arg is None

    def test_parse_command_empty(self):
        """Test parsing empty message."""
        from src.agents.fastlane import FastLaneCommands

        commands = FastLaneCommands()

        cmd, arg, full_arg = commands.parse_command("")

        assert cmd == ""
        assert arg is None

    def test_command_list(self):
        """Test COMMANDS list contains expected commands."""
        from src.agents.fastlane import FastLaneCommands

        assert "status" in FastLaneCommands.COMMANDS
        assert "help" in FastLaneCommands.COMMANDS
        assert "thinking" not in FastLaneCommands.COMMANDS
        assert "reasoning" not in FastLaneCommands.COMMANDS


class TestFastLaneProcess:
    """Tests for fastlane command processing."""

    def test_process_status(self):
        """Test /status command."""
        from src.agents.fastlane import FastLaneCommands

        commands = FastLaneCommands(agent=MagicMock())

        response = commands._cmd_status()

        assert "📊" in response
        assert "Current Configuration" in response

    def test_process_help(self):
        """Test /help command."""
        from src.agents.fastlane import FastLaneCommands

        commands = FastLaneCommands()

        response = commands._cmd_help()

        assert "🤖" in response
        assert "Fast Lane Commands" in response
        assert "/status" in response

    @pytest.mark.asyncio
    async def test_process_removed_command_returns_none(self):
        """Test that removed /thinking command is not handled."""
        from src.agents.fastlane import FastLaneCommands

        commands = FastLaneCommands()

        response = await commands.process("/thinking high")

        assert response is None


class TestFastLaneGlobalFunctions:
    """Tests for global fastlane functions."""

    def test_get_fastlane(self):
        """Test getting global fastlane instance."""
        from src.agents.fastlane import get_fastlane
        import src.agents.fastlane as fastlane_mod

        # Reset global
        fastlane_mod._fastlane = None

        fastlane = get_fastlane()

        assert fastlane is not None
        assert isinstance(fastlane, fastlane_mod.FastLaneCommands)

    @pytest.mark.asyncio
    async def test_process_fastlane_command(self):
        """Test process_fastlane_command function."""
        from src.agents.fastlane import process_fastlane_command

        # Non-command should return None
        response = await process_fastlane_command("hello world")

        assert response is None

    def test_is_fastlane_command(self):
        """Test is_fastlane_command function."""
        from src.agents.fastlane import is_fastlane_command

        assert is_fastlane_command("/status") is True
        assert is_fastlane_command("hello") is False
