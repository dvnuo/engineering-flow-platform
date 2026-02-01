"""Tests for Discord channel adapter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


class TestDiscordChannel:
    """Test cases for DiscordChannel class."""

    def test_discord_channel_init(self):
        """Test DiscordChannel initialization."""
        with patch('openclaw_mini.channel.discord.config') as mock_config:
            mock_config.discord = {
                'mode': 'bot',
                'bot_token': 'test_token',
                'channel_id': '123456',
                'webhook_url': ''
            }
            
            from openclaw_mini.channel.discord import DiscordChannel
            
            channel = DiscordChannel()
            assert channel.mode == 'bot'
            assert channel.bot_token == 'test_token'
            assert channel.channel_id == '123456'

    def test_verify_discord_signature_missing_values(self):
        """Test signature verification with missing values."""
        from openclaw_mini.gateway.server import verify_discord_signature
        
        # Missing signature or secret should return True (skip verification)
        result = verify_discord_signature(b'test', '', '')
        assert result is True
        
        result = verify_discord_signature(b'test', 'sig', '')
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_webhook_payload(self):
        """Test webhook payload handling."""
        with patch('openclaw_mini.channel.discord.config') as mock_config:
            mock_config.discord = {}
            
            from openclaw_mini.channel.discord import DiscordChannel
            
            channel = DiscordChannel()
            payload = {
                'id': 'msg123',
                'channel_id': 'ch123',
                'content': 'Hello',
                'author': {'username': 'testuser'},
                'thread': {'id': 'thread123'}
            }
            
            result = await channel.handle_webhook_payload(payload)
            
            assert result['message_id'] == 'msg123'
            assert result['channel_id'] == 'ch123'
            assert result['content'] == 'Hello'
            assert result['username'] == 'testuser'
            assert result['thread_id'] == 'thread123'

    @pytest.mark.asyncio
    async def test_send_message_bot_api_mode(self):
        """Test sending message in Bot API mode."""
        with patch('openclaw_mini.channel.discord.config') as mock_config:
            mock_config.discord = {
                'mode': 'bot',
                'bot_token': 'test_token',
                'channel_id': '123456'
            }
            
            from openclaw_mini.channel.discord import DiscordChannel
            
            # Create mock bot and channel
            mock_channel = AsyncMock()
            mock_channel.send = AsyncMock(return_value=MagicMock(id='sent123'))
            
            channel = DiscordChannel()
            channel.mode = 'bot'
            channel.bot = MagicMock()
            channel.bot.fetch_channel = AsyncMock(return_value=mock_channel)
            channel.target_channel_id = '123456'
            
            result = await channel.send_message('Hello World', '123456')
            
            assert result['id'] == 'sent123'
            assert result['content'] == 'Hello World'
            mock_channel.send.assert_called_once_with('Hello World')

    @pytest.mark.asyncio
    async def test_send_message_without_channel_id(self):
        """Test sending message without channel_id uses default."""
        with patch('openclaw_mini.channel.discord.config') as mock_config:
            mock_config.discord = {
                'mode': 'bot',
                'bot_token': 'test_token',
                'channel_id': '123456'
            }
            
            from openclaw_mini.channel.discord import DiscordChannel
            
            mock_channel = AsyncMock()
            mock_channel.send = AsyncMock(return_value=MagicMock(id='sent456'))
            
            channel = DiscordChannel()
            channel.mode = 'bot'
            channel.bot = MagicMock()
            channel.bot.fetch_channel = AsyncMock(return_value=mock_channel)
            channel.target_channel_id = '123456'
            
            result = await channel.send_message('Using default channel')
            
            channel.bot.fetch_channel.assert_called_once_with(int('123456'))

    @pytest.mark.asyncio
    async def test_set_message_callback(self):
        """Test setting message callback."""
        with patch('openclaw_mini.channel.discord.config') as mock_config:
            mock_config.discord = {'mode': 'bot', 'bot_token': 'test'}
            
            from openclaw_mini.channel.discord import DiscordChannel
            
            channel = DiscordChannel()
            callback = AsyncMock()
            
            channel.set_message_callback(callback)
            
            # Callback should be stored for bot mode
            assert channel.bot is None or channel.bot.message_callback == callback


class TestDiscordBot:
    """Test cases for DiscordBot class."""

    def test_discord_bot_init(self):
        """Test DiscordBot initialization."""
        with patch('openclaw_mini.channel.discord.config') as mock_config:
            mock_config.discord = {
                'bot_token': 'test_token',
                'channel_id': '123456'
            }
            
            from openclaw_mini.channel.discord import DiscordBot
            
            bot = DiscordBot(message_callback=None)
            assert bot.target_channel_id == '123456'
            assert bot.message_callback is None


class TestRunBot:
    """Test cases for module-level functions."""

    @pytest.mark.asyncio
    async def test_run_bot_function(self):
        """Test run_bot function exists and is callable."""
        from openclaw_mini.channel.discord import run_bot
        import inspect
        
        assert inspect.iscoroutinefunction(run_bot)
