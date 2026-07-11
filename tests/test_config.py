"""Tests for Config class."""

import os
import tempfile

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
            f.write("jira:\n  enabled: true\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("jira.enabled") is True
            
            os.unlink(f.name)

    def test_config_get_default(self):
        """Test getting default value for missing keys."""
        config = Config()
        assert config.get("nonexistent.key", "default") == "default"


class TestConfigStaticLoad:
    """The base config is read once at boot; there is no hot reload."""

    def test_config_nonexistent_file(self):
        """Test config with nonexistent file."""
        config = Config("/nonexistent/path/config.yaml")
        assert config._config == {}

    def test_config_get_does_not_reload_on_file_change(self, tmp_path):
        """Config values stay stable after boot even when the file changes."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("model: gpt-4o\n", encoding="utf-8")
        config = Config(str(config_path))

        assert config.get("model") == "gpt-4o"

        config_path.write_text("model: claude-3-5-sonnet\n", encoding="utf-8")

        # No mtime auto-reload: a pod restart is the only delivery path.
        assert config.get("model") == "gpt-4o"


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
            f.write("jira:\n  enabled: true\nllm:\n  api_key: key\n")
            f.flush()
            
            config = Config(f.name)
            assert config.jira["enabled"] is True
            assert config.llm["api_key"] == "key"
            
            os.unlink(f.name)


class TestConfigProxy:
    """Tests for proxy configuration handling."""

    PROXY_ENV_KEYS = [
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ]

    def _prepare_proxy_env(self, monkeypatch, clear: bool = True):
        """Ensure proxy env vars are tracked and optionally cleared for test isolation."""
        for key in self.PROXY_ENV_KEYS:
            monkeypatch.setenv(key, "")
            if clear:
                monkeypatch.delenv(key, raising=False)

    def test_apply_proxy_with_plain_credentials(self, tmp_path, monkeypatch):
        """Test apply_proxy() with plain username/password credentials."""
        self._prepare_proxy_env(monkeypatch)

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "proxy:\n"
            "  enabled: true\n"
            "  url: http://proxy.example.com:8080\n"
            "  username: user\n"
            "  password: pass\n"
        )
        config = Config(str(config_path))
        config.apply_proxy()

        expected_url = "http://user:pass@proxy.example.com:8080"
        assert os.environ["http_proxy"] == expected_url
        assert os.environ["https_proxy"] == expected_url
        assert os.environ["HTTP_PROXY"] == expected_url
        assert os.environ["HTTPS_PROXY"] == expected_url
        assert os.environ["all_proxy"] == expected_url
        assert os.environ["ALL_PROXY"] == expected_url

    def test_apply_proxy_with_special_characters_in_credentials(self, tmp_path, monkeypatch):
        """Test apply_proxy() URL-encodes special characters in credentials."""
        self._prepare_proxy_env(monkeypatch)

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "proxy:\n"
            "  enabled: true\n"
            "  url: http://proxy.example.com:8080\n"
            "  username: user@name\n"
            "  password: p:a/s?s#%word\n"
        )
        config = Config(str(config_path))
        config.apply_proxy()

        expected_url = "http://user%40name:p%3Aa%2Fs%3Fs%23%25word@proxy.example.com:8080"
        assert os.environ["http_proxy"] == expected_url
        assert os.environ["https_proxy"] == expected_url
        assert os.environ["HTTP_PROXY"] == expected_url
        assert os.environ["HTTPS_PROXY"] == expected_url
        assert os.environ["all_proxy"] == expected_url
        assert os.environ["ALL_PROXY"] == expected_url

    def test_apply_proxy_disabled_clears_proxy_env_when_proxy_section_exists(self, tmp_path, monkeypatch):
        """Test disabled proxy clears proxy-related env vars when section exists."""
        self._prepare_proxy_env(monkeypatch)
        for key in self.PROXY_ENV_KEYS:
            monkeypatch.setenv(key, "http://should-be-cleared")

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "proxy:\n"
            "  enabled: false\n"
            "  url: http://proxy.example.com:8080\n"
        )
        config = Config(str(config_path))
        config.apply_proxy()

        for key in self.PROXY_ENV_KEYS:
            assert key not in os.environ

    def test_apply_proxy_ipv6_host_preserves_brackets(self, tmp_path, monkeypatch):
        """Test apply_proxy() preserves brackets for IPv6 proxy hosts."""
        self._prepare_proxy_env(monkeypatch)

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "proxy:\n"
            "  enabled: true\n"
            "  url: http://[2001:db8::1]:8080\n"
            "  username: user\n"
            "  password: pass\n"
        )
        config = Config(str(config_path))
        config.apply_proxy()

        expected_url = "http://user:pass@[2001:db8::1]:8080"
        assert os.environ["http_proxy"] == expected_url

    def test_apply_proxy_replaces_existing_userinfo(self, tmp_path, monkeypatch):
        """Test apply_proxy() replaces existing URL userinfo with configured credentials."""
        self._prepare_proxy_env(monkeypatch)

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "proxy:\n"
            "  enabled: true\n"
            "  url: http://olduser:oldpass@proxy.example.com:8080\n"
            "  username: newuser\n"
            "  password: newpass\n"
        )
        config = Config(str(config_path))
        config.apply_proxy()

        expected_url = "http://newuser:newpass@proxy.example.com:8080"
        assert os.environ["http_proxy"] == expected_url

    def test_apply_proxy_malformed_or_schemeless_url_not_rewritten_to_none(self, tmp_path, monkeypatch):
        """Test malformed/scheme-less proxy URLs are not rewritten to include @None."""
        self._prepare_proxy_env(monkeypatch)

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "proxy:\n"
            "  enabled: true\n"
            "  url: proxy.example.com:8080\n"
            "  username: user\n"
            "  password: pass\n"
        )
        config = Config(str(config_path))
        config.apply_proxy()

        assert os.environ["http_proxy"] == "proxy.example.com:8080"
        assert "@None" not in os.environ["http_proxy"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
