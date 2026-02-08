"""Tests for Fast Lane Commands module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestFastLaneCommands:
    """Tests for FastLaneCommands class."""
    
    def test_is_command_with_slash(self):
        """Test is_command returns True for commands."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        assert commands.is_command("/thinking high") is True
        assert commands.is_command("/reasoning on") is True
        assert commands.is_command("/status") is True
        assert commands.is_command("/help") is True
    
    def test_is_command_without_slash(self):
        """Test is_command returns False for non-commands."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        assert commands.is_command("hello") is False
        assert commands.is_command("what is the weather") is False
        assert commands.is_command("") is False
    
    def test_parse_command_thinking(self):
        """Test parsing /thinking command."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        cmd, arg, full_arg = commands.parse_command("/thinking high")
        
        assert cmd == "thinking"
        assert arg == "high"
        assert full_arg is None
    
    def test_parse_command_with_full_arg(self):
        """Test parsing command with full argument."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        cmd, arg, full_arg = commands.parse_command("/thinking medium with extra")
        
        assert cmd == "thinking"
        assert arg == "medium"
        assert full_arg == "with extra"
    
    def test_parse_command_reasoning(self):
        """Test parsing /reasoning command."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        cmd, arg, full_arg = commands.parse_command("/reasoning on")
        
        assert cmd == "reasoning"
        assert arg == "on"
    
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
        
        assert "thinking" in FastLaneCommands.COMMANDS
        assert "reasoning" in FastLaneCommands.COMMANDS
        assert "status" in FastLaneCommands.COMMANDS
        assert "help" in FastLaneCommands.COMMANDS
    
    def test_thinking_levels(self):
        """Test thinking levels are defined."""
        from src.agents.fastlane import FastLaneCommands
        
        assert "off" in FastLaneCommands.THINKING_LEVELS
        assert "minimal" in FastLaneCommands.THINKING_LEVELS
        assert "low" in FastLaneCommands.THINKING_LEVELS
        assert "medium" in FastLaneCommands.THINKING_LEVELS
        assert "high" in FastLaneCommands.THINKING_LEVELS


class TestFastLaneProcess:
    """Tests for fastlane command processing."""
    
    @pytest.mark.asyncio
    async def test_process_thinking_level(self):
        """Test processing /thinking command."""
        from src.agents.fastlane import FastLaneCommands
        from src.agents.thinking import ThinkLevel
        
        # Mock agent
        mock_agent = MagicMock()
        mock_agent.think_level = ThinkLevel.OFF
        
        commands = FastLaneCommands(agent=mock_agent)
        
        response = await commands._cmd_thinking("high")
        
        assert "✅" in response
        assert "high" in response
        assert mock_agent.think_level == ThinkLevel.HIGH
    
    @pytest.mark.asyncio
    async def test_process_thinking_invalid_level(self):
        """Test /thinking with invalid level."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        response = await commands._cmd_thinking("invalid_level")
        
        assert "❌" in response
        assert "Invalid" in response
    
    @pytest.mark.asyncio
    async def test_process_thinking_no_level(self):
        """Test /thinking without level."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        response = await commands._cmd_thinking(None)
        
        assert "📖" in response
        assert "Thinking Levels" in response
    
    @pytest.mark.asyncio
    async def test_process_reasoning_on(self):
        """Test /reasoning on command."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        response = await commands._cmd_reasoning("on")
        
        assert "on" in response.lower()
    
    @pytest.mark.asyncio
    async def test_process_reasoning_off(self):
        """Test /reasoning off command."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        response = await commands._cmd_reasoning("off")
        
        # Check response contains "disabled" or "off"
        response_lower = response.lower()
        assert "disabled" in response_lower or "off" in response_lower
    
    @pytest.mark.asyncio
    async def test_process_reasoning_invalid(self):
        """Test /reasoning with invalid argument."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        response = await commands._cmd_reasoning("maybe")
        
        assert "📖" in response
        assert "Reasoning" in response
    
    def test_process_status(self):
        """Test /status command."""
        from src.agents.fastlane import FastLaneCommands
        from src.agents.thinking import ThinkLevel
        
        # Mock agent
        mock_agent = MagicMock()
        mock_agent.think_level = ThinkLevel.HIGH
        
        commands = FastLaneCommands(agent=mock_agent)
        
        response = commands._cmd_status()
        
        assert "📊" in response
        assert "Current Configuration" in response
        assert "high" in response
    
    def test_process_help(self):
        """Test /help command."""
        from src.agents.fastlane import FastLaneCommands
        
        commands = FastLaneCommands()
        
        response = commands._cmd_help()
        
        assert "🤖" in response
        assert "Fast Lane Commands" in response
        assert "/thinking" in response
        assert "/status" in response


class TestFastLaneGlobalFunctions:
    """Tests for global fastlane functions."""
    
    def test_get_fastlane(self):
        """Test getting global fastlane instance."""
        from src.agents.fastlane import get_fastlane, _fastlane
        import agent.fastlane
        
        # Reset global
        agent.fastlane._fastlane = None
        
        fastlane = get_fastlane()
        
        assert fastlane is not None
        assert isinstance(fastlane, agent.fastlane.FastLaneCommands)
    
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
        
        assert is_fastlane_command("/thinking high") is True
        assert is_fastlane_command("hello") is False


class TestFastLaneIntegration:
    """Integration tests for fastlane with Agent."""
    
    @pytest.mark.asyncio
    async def test_process_with_fastlane_command(self):
        """Test Agent.process handles fastlane commands."""
        from src.agents.core import Agent
        from src.agents.fastlane import _fastlane
        import agent.fastlane
        
        # Reset global
        agent.fastlane._fastlane = None
        
        agent = Agent()
        
        response = await agent.process(
            message="/status",
            session_id="test-session"
        )
        
        assert response is not None
        assert "response" in response
        assert "📊" in response["response"] or "Configuration" in response["response"]
    
    @pytest.mark.asyncio
    async def test_process_thinking_updates_agent(self):
        """Test /thinking updates agent's think_level."""
        from src.agents.core import Agent
        from src.agents.thinking import ThinkLevel
        from src.agents.fastlane import _fastlane
        import agent.fastlane
        
        # Reset global
        agent.fastlane._fastlane = None
        
        agent = Agent()
        original_level = agent.think_level
        
        # Change thinking level
        response = await agent.process(
            message="/thinking high",
            session_id="test-session"
        )
        
        assert agent.think_level == ThinkLevel.HIGH
        assert original_level != ThinkLevel.HIGH
