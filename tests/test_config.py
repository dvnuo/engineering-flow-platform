"""Tests for Config class and hot reload functionality."""

import os
import tempfile
import time
from pathlib import Path

import pytest

from src.config import Config


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


class TestConfigProxy:
    """Tests for proxy configuration handling."""

    def test_apply_proxy_with_plain_credentials(self):
        """Test apply_proxy() with plain username/password credentials."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(
                "proxy:\n"
                "  enabled: true\n"
                "  url: http://proxy.example.com:8080\n"
                "  username: user\n"
                "  password: pass\n"
            )
            f.flush()
            config = Config(f.name)
            config.apply_proxy()

            expected_url = "http://user:pass@proxy.example.com:8080"
            assert os.environ["http_proxy"] == expected_url
            assert os.environ["https_proxy"] == expected_url
            assert os.environ["HTTP_PROXY"] == expected_url
            assert os.environ["HTTPS_PROXY"] == expected_url

            os.unlink(f.name)

    def test_apply_proxy_with_special_characters_in_credentials(self):
        """Test apply_proxy() URL-encodes special characters in credentials."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(
                "proxy:\n"
                "  enabled: true\n"
                "  url: http://proxy.example.com:8080\n"
                "  username: user@name\n"
                "  password: p:a/s?s#%word\n"
            )
            f.flush()
            config = Config(f.name)
            config.apply_proxy()

            expected_url = "http://user%40name:p%3Aa%2Fs%3Fs%23%25word@proxy.example.com:8080"
            assert os.environ["http_proxy"] == expected_url
            assert os.environ["https_proxy"] == expected_url
            assert os.environ["HTTP_PROXY"] == expected_url
            assert os.environ["HTTPS_PROXY"] == expected_url

            os.unlink(f.name)

    def test_apply_proxy_disabled_clears_proxy_env_when_proxy_section_exists(self):
        """Test disabled proxy clears proxy-related env vars when section exists."""
        for var in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"]:
            os.environ[var] = "http://should-be-cleared"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(
                "proxy:\n"
                "  enabled: false\n"
                "  url: http://proxy.example.com:8080\n"
            )
            f.flush()
            config = Config(f.name)
            config.apply_proxy()

            for var in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"]:
                assert var not in os.environ

            os.unlink(f.name)

    def test_apply_proxy_ipv6_host_preserves_brackets(self):
        """Test apply_proxy() preserves brackets for IPv6 proxy hosts."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(
                "proxy:\n"
                "  enabled: true\n"
                "  url: http://[2001:db8::1]:8080\n"
                "  username: user\n"
                "  password: pass\n"
            )
            f.flush()
            config = Config(f.name)
            config.apply_proxy()

            expected_url = "http://user:pass@[2001:db8::1]:8080"
            assert os.environ["http_proxy"] == expected_url

            os.unlink(f.name)

    def test_apply_proxy_replaces_existing_userinfo(self):
        """Test apply_proxy() replaces existing URL userinfo with configured credentials."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(
                "proxy:\n"
                "  enabled: true\n"
                "  url: http://olduser:oldpass@proxy.example.com:8080\n"
                "  username: newuser\n"
                "  password: newpass\n"
            )
            f.flush()
            config = Config(f.name)
            config.apply_proxy()

            expected_url = "http://newuser:newpass@proxy.example.com:8080"
            assert os.environ["http_proxy"] == expected_url

            os.unlink(f.name)

    def test_apply_proxy_malformed_or_schemeless_url_not_rewritten_to_none(self):
        """Test malformed/scheme-less proxy URLs are not rewritten to include @None."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(
                "proxy:\n"
                "  enabled: true\n"
                "  url: proxy.example.com:8080\n"
                "  username: user\n"
                "  password: pass\n"
            )
            f.flush()
            config = Config(f.name)
            config.apply_proxy()

            assert os.environ["http_proxy"] == "proxy.example.com:8080"
            assert "@None" not in os.environ["http_proxy"]

            os.unlink(f.name)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
