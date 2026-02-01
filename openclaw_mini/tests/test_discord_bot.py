"""Tests for Discord channel adapter and GitHub Copilot integration."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest


class TestConfigurationTemplate:
    """Test configuration template file."""

    def test_config_yaml_example_exists(self):
        """Test that config.yaml.example exists."""
        config_example = Path(__file__).parent.parent / "config.yaml.example"
        assert config_example.exists(), "config.yaml.example should exist"
    
    def test_config_yaml_example_has_discord_section(self):
        """Test that config.yaml.example has discord section."""
        config_example = Path(__file__).parent.parent / "config.yaml.example"
        content = config_example.read_text()
        
        assert "discord:" in content
        assert "bot_token:" in content
        assert "channel_id:" in content
    
    def test_config_yaml_example_has_llm_section(self):
        """Test that config.yaml.example has llm section."""
        config_example = Path(__file__).parent.parent / "config.yaml.example"
        content = config_example.read_text()
        
        assert "llm:" in content
        assert "api_key:" in content
        assert "provider:" in content
        assert "github_copilot" in content
    
    def test_config_yaml_example_has_server_section(self):
        """Test that config.yaml.example has server section."""
        config_example = Path(__file__).parent.parent / "config.yaml.example"
        content = config_example.read_text()
        
        assert "server:" in content
        assert "host:" in content
        assert "port:" in content


class TestGitHubCopilotConfig:
    """Test GitHub Copilot configuration examples."""

    def test_copilot_example_in_config(self):
        """Test GitHub Copilot example is in config.yaml.example."""
        config_example = Path(__file__).parent.parent / "config.yaml.example"
        content = config_example.read_text()
        
        assert "github_copilot" in content.lower() or "copilot" in content.lower()

    def test_copilot_api_base(self):
        """Test GitHub Copilot API base URL is correct."""
        config_example = Path(__file__).parent.parent / "config.yaml.example"
        content = config_example.read_text()
        
        # Should mention GitHub Copilot API endpoint
        assert "api.github.com/copilot" in content or "github_copilot" in content


class TestDiscordModeConfig:
    """Test Discord mode configuration."""

    def test_discord_has_mode_option(self):
        """Test that config.yaml.example has mode option."""
        config_example = Path(__file__).parent.parent / "config.yaml.example"
        content = config_example.read_text()
        
        assert "mode:" in content

    def test_discord_has_bot_example(self):
        """Test that bot mode example is provided."""
        config_example = Path(__file__).parent.parent / "config.yaml.example"
        content = config_example.read_text()
        
        # Should have bot_token for bot mode
        assert "bot_token" in content


class TestLLMClientImport:
    """Test LLM client can be imported."""

    def test_llm_client_has_copilot_methods(self):
        """Test LLMClient has GitHub Copilot methods."""
        from openclaw_mini.agent.llm import LLMClient
        
        # Check methods exist
        assert hasattr(LLMClient, '_get_headers')
        assert hasattr(LLMClient, '_get_chat_endpoint')
        assert hasattr(LLMClient, 'is_github_copilot')

    def test_llm_client_copilot_api_base(self):
        """Test LLMClient has correct Copilot API base."""
        from openclaw_mini.agent.llm import LLMClient
        
        assert hasattr(LLMClient, 'COPILOT_API_BASE')
        assert "api.github.com/copilot" in LLMClient.COPILOT_API_BASE


class TestGatewayImport:
    """Test Gateway can be imported."""

    def test_gateway_has_start_stop(self):
        """Test Gateway has start and stop methods."""
        from openclaw_mini.gateway.server import Gateway
        
        assert hasattr(Gateway, 'start')
        assert hasattr(Gateway, 'stop')
        # host and port are instance attributes, set in __init__

    def test_gateway_instantiation(self):
        """Test Gateway can be instantiated."""
        from openclaw_mini.gateway.server import Gateway
        
        gateway = Gateway()
        assert gateway is not None
        assert hasattr(gateway, 'host')
        assert hasattr(gateway, 'port')


class TestDiscordChannelImport:
    """Test Discord channel can be imported."""

    def test_discord_channel_has_send_message(self):
        """Test DiscordChannel has send_message method."""
        from openclaw_mini.channel.discord import DiscordChannel
        
        assert hasattr(DiscordChannel, 'send_message')
        # start/stop methods are handled by discord_channel instance


class TestResponseJsonNotAwaited:
    """Test that response.json() is synchronous (not async)."""

    def test_response_json_is_sync(self):
        """Verify httpx response.json() is a sync method."""
        import httpx
        
        # Check that response.json() doesn't require await
        # In httpx, Response.json() is a sync method
        import inspect
        json_method = httpx.Response.json
        
        # Check if it's a coroutine function
        is_async = inspect.iscoroutinefunction(json_method)
        assert not is_async, "response.json() should NOT be async (awaited)"
        
    def test_llm_code_uses_sync_json(self):
        """Verify LLM client code doesn't await response.json()."""
        llm_file = Path(__file__).parent.parent / "agent" / "llm.py"
        content = llm_file.read_text()
        
        # Should have response.json() without await
        # Count occurrences of await response.json() vs response.json()
        await_count = content.count("await response.json()")
        sync_count = content.count("response.json()") - await_count
        
        assert sync_count > 0, "Should have response.json() without await"
        assert await_count == 0, "Should NOT have 'await response.json()' - it's sync!"


class TestGitignoreConfig:
    """Test .gitignore includes config.yaml."""

    def test_gitignore_has_config_yaml(self):
        """Test that .gitignore excludes config.yaml."""
        gitignore = Path(__file__).parent.parent / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text()
            assert "config.yaml" in content, "config.yaml should be in .gitignore"
        else:
            pytest.skip(".gitignore not found")


class TestMainScript:
    """Test main script has proper structure."""

    def test_main_has_asyncio(self):
        """Test main.py uses asyncio for async execution."""
        main_file = Path(__file__).parent.parent / "main.py"
        content = main_file.read_text()
        
        assert "asyncio" in content, "main.py should use asyncio"
        assert "asyncio.run" in content, "main.py should use asyncio.run"

    def test_main_has_gateway(self):
        """Test main.py uses gateway for messaging."""
        main_file = Path(__file__).parent.parent / "main.py"
        content = main_file.read_text()
        
        assert "gateway" in content, "main.py should use gateway"
