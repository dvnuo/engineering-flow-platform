"""Tests for Config class and hot reload functionality."""

import os
import tempfile
import time
from pathlib import Path

import pytest

from config import Config


class TestConfigBasic:
    """Basic configuration tests."""

    def test_config_load_default(self):
        """Test loading default config."""
        config = Config()
        assert isinstance(config._config, dict)

    def test_config_get_nested(self):
        """Test getting nested config values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("discord:\n  bot_token: test_token\n  channel_id: 123\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("discord.bot_token") == "test_token"
            assert config.get("discord.channel_id") == 123
            
            os.unlink(f.name)

    def test_config_get_default(self):
        """Test getting default value for missing keys."""
        config = Config()
        assert config.get("nonexistent.key", "default") == "default"


class TestConfigHotReload:
    """Tests for configuration hot reload feature."""

    def test_config_reload_detects_changes(self):
        """Test that reload() detects file changes."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("initial: value1\n")
            f.flush()
            config = Config(f.name)
            
            # Initial value
            assert config.get("initial") == "value1"
            
            # Modify file
            time.sleep(0.1)  # Ensure mtime changes
            with open(f.name, 'w') as wf:
                wf.write("initial: value2\n")
            
            # Reload should detect change
            reloaded = config.reload()
            assert reloaded is True
            assert config.get("initial") == "value2"
            
            os.unlink(f.name)

    def test_config_reload_no_changes(self):
        """Test that reload() returns False when no changes."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("key: value\n")
            f.flush()
            config = Config(f.name)
            
            # No changes should return False
            reloaded = config.reload()
            assert reloaded is False
            
            os.unlink(f.name)

    def test_config_auto_reload_on_get(self):
        """Test that config auto-reloads when accessed via get()."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("model: gpt-4o\n")
            f.flush()
            config = Config(f.name)
            
            # Initial value
            assert config.get("model") == "gpt-4o"
            
            # Modify file
            time.sleep(0.1)
            with open(f.name, 'w') as wf:
                wf.write("model: claude-3-5-sonnet\n")
            
            # Access via get() should trigger auto-reload
            value = config.get("model")
            assert value == "claude-3-5-sonnet"
            
            os.unlink(f.name)

    def test_config_nonexistent_file(self):
        """Test config with nonexistent file."""
        config = Config("/nonexistent/path/config.yaml")
        assert config._config == {}
        assert config.reload() is False

    def test_config_timestamp_tracking(self):
        """Test that _last_modified is set correctly."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("key: value\n")
            f.flush()
            config = Config(f.name)
            
            assert config._last_modified > 0
            
            os.unlink(f.name)


class TestConfigEdgeCases:
    """Edge case tests for Config class."""

    def test_config_empty_file(self):
        """Test config with empty file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("")
            f.flush()
            
            config = Config(f.name)
            assert config._config == {}
            
            os.unlink(f.name)

    def test_config_deep_nesting(self):
        """Test deeply nested config values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("level1:\n  level2:\n    level3:\n      value: deep\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("level1.level2.level3.value") == "deep"
            
            os.unlink(f.name)

    def test_config_property_accessors(self):
        """Test config property accessors."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("discord:\n  bot_token: token\nllm:\n  api_key: key\n")
            f.flush()
            
            config = Config(f.name)
            assert config.discord["bot_token"] == "token"
            assert config.llm["api_key"] == "key"
            
            os.unlink(f.name)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
