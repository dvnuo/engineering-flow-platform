"""Tests for Config loader."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from openclaw_mini.config import Config
import tempfile
import os


def test_config_load_default():
    """Test loading default config."""
    config = Config()
    assert isinstance(config._config, dict)


def test_config_get():
    """Test getting config values."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("discord:\n  bot_token: test_token\n  channel_id: 123\n")
        f.flush()
        
        config = Config(f.name)
        assert config.get("discord.bot_token") == "test_token"
        assert config.get("discord.channel_id") == "123"
        assert config.get("discord.nonexistent", "default") == "default"
        
        os.unlink(f.name)


def test_config_dot_notation():
    """Test dot notation for nested config."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("llm:\n  api:\n    base: https://api.example.com\n")
        f.flush()
        
        config = Config(f.name)
        assert config.get("llm.api.base") == "https://api.example.com"
        
        os.unlink(f.name)


def test_config_properties():
    """Test config property accessors."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write("discord:\n  bot_token: token\nllm:\n  api_key: key\nserver:\n  port: 8080\n")
        f.flush()
        
        config = Config(f.name)
        assert config.discord["bot_token"] == "token"
        assert config.llm["api_key"] == "key"
        assert config.server["port"] == 8080
        
        os.unlink(f.name)


def test_config_missing_file():
    """Test config with missing file returns empty dict."""
    config = Config("/nonexistent/path/config.yaml")
    assert config._config == {}


if __name__ == "__main__":
    test_config_load_default()
    test_config_get()
    test_config_dot_notation()
    test_config_properties()
    test_config_missing_file()
    print("All config tests passed!")
