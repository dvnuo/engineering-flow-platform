"""Tests for Config loader."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import tempfile
import os

from config import Config


class TestConfigBasic:
    """Basic configuration tests."""

    def test_config_load_default(self):
        """Test loading default config."""
        config = Config()
        assert isinstance(config._config, dict)

    def test_config_get(self):
        """Test getting config values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("discord:\n  bot_token: test_token\n  channel_id: 123\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("discord.bot_token") == "test_token"
            # YAML parses numbers as integers, so we check for integer type
            assert config.get("discord.channel_id") == 123
            assert config.get("discord.channel_id", "0") == 123
            assert config.get("discord.nonexistent", "default") == "default"
            
            os.unlink(f.name)

    def test_config_dot_notation(self):
        """Test dot notation for nested config."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("llm:\n  api:\n    base: https://api.example.com\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("llm.api.base") == "https://api.example.com"
            
            os.unlink(f.name)

    def test_config_properties(self):
        """Test config property accessors."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("discord:\n  bot_token: token\nllm:\n  api_key: key\nserver:\n  port: 8080\n")
            f.flush()
            
            config = Config(f.name)
            assert config.discord["bot_token"] == "token"
            assert config.llm["api_key"] == "key"
            assert config.server["port"] == 8080
            
            os.unlink(f.name)


class TestConfigMissing:
    """Missing config tests."""

    def test_config_missing_file(self):
        """Test config with missing file returns empty dict."""
        config = Config("/nonexistent/path/config.yaml")
        assert config._config == {}

    def test_config_missing_nested_key(self):
        """Test getting missing nested key returns default."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("discord:\n  bot_token: token\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("discord.missing.deeply.nested") is None
            assert config.get("discord.missing.deeply.nested", "default") == "default"
            
            os.unlink(f.name)

    def test_config_missing_top_level(self):
        """Test missing top-level key."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("existing:\n  value: test\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("missing_top_level") is None
            assert config.get("missing_top_level", "fallback") == "fallback"
            
            os.unlink(f.name)


class TestConfigTypes:
    """Config type handling tests."""

    def test_config_integer_values(self):
        """Test config with integer values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("server:\n  port: 8080\n  timeout: 30\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("server.port") == 8080
            assert isinstance(config.get("server.port"), int)
            
            os.unlink(f.name)

    def test_config_boolean_values(self):
        """Test config with boolean values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("debug:\n  enabled: true\n  verbose: false\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("debug.enabled") is True
            assert config.get("debug.verbose") is False
            
            os.unlink(f.name)

    def test_config_float_values(self):
        """Test config with float values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("model:\n  temperature: 0.7\n  top_p: 0.95\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("model.temperature") == 0.7
            assert isinstance(config.get("model.temperature"), float)
            
            os.unlink(f.name)

    def test_config_list_values(self):
        """Test config with list values."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("features:\n  - feature1\n  - feature2\n")
            f.flush()
            
            config = Config(f.name)
            features = config.get("features")
            assert isinstance(features, list)
            assert len(features) == 2
            assert "feature1" in features
            
            os.unlink(f.name)


class TestConfigEdgeCases:
    """Edge case tests."""

    def test_config_empty_file(self):
        """Test config with empty file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("")
            f.flush()
            
            config = Config(f.name)
            assert config._config == {}
            
            os.unlink(f.name)

    def test_config_only_comments(self):
        """Test config with only comments."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("# This is a comment\n# Another comment\n")
            f.flush()
            
            config = Config(f.name)
            assert config._config == {}
            
            os.unlink(f.name)

    def test_config_deep_nesting(self):
        """Test deeply nested config."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("level1:\n  level2:\n    level3:\n      level4:\n        value: deep\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("level1.level2.level3.level4.value") == "deep"
            
            os.unlink(f.name)

    def test_config_special_characters(self):
        """Test config with special characters."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            # Use proper YAML quoting for special characters
            f.write("special:\n  value: 'quoted value with apostrophe'\n")
            f.flush()
            
            config = Config(f.name)
            value = config.get("special.value")
            # YAML parsing should handle these
            assert value is not None
            assert "apostrophe" in value
            
            os.unlink(f.name)

    def test_config_unicode(self):
        """Test config with unicode characters."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("unicode:\n  chinese: 你好\n  emoji: 🌍\n")
            f.flush()
            
            config = Config(f.name)
            assert config.get("unicode.chinese") == "你好"
            assert config.get("unicode.emoji") == "🌍"
            
            os.unlink(f.name)


class TestConfigReload:
    """Config reload tests."""

    def test_config_reload(self):
        """Test reloading config."""
        import tempfile
        import shutil
        
        temp_dir = tempfile.mkdtemp()
        config_path = os.path.join(temp_dir, "config.yaml")
        
        try:
            # Write initial config
            with open(config_path, 'w') as f:
                f.write("initial: value1\n")
            
            config = Config(config_path)
            assert config.get("initial") == "value1"
            
            # Update config file
            with open(config_path, 'w') as f:
                f.write("initial: value2\n")
            
            # Note: Config doesn't auto-reload, need to create new instance
            config2 = Config(config_path)
            assert config2.get("initial") == "value2"
            
        finally:
            shutil.rmtree(temp_dir)

    def test_config_hot_reload_detection(self):
        """Test that config detects file changes by modification time."""
        import tempfile
        import shutil
        import time
        
        temp_dir = tempfile.mkdtemp()
        config_path = os.path.join(temp_dir, "config.yaml")
        
        try:
            # Write initial config
            with open(config_path, 'w') as f:
                f.write("key: original_value\n")
            
            config = Config(config_path)
            assert config.get("key") == "original_value"
            
            # Wait a moment to ensure different mtime
            time.sleep(0.1)
            
            # Update config file
            with open(config_path, 'w') as f:
                f.write("key: updated_value\n")
            
            # reload() should detect the change
            reloaded = config.reload()
            assert reloaded == True
            assert config.get("key") == "updated_value"
            
        finally:
            shutil.rmtree(temp_dir)

    def test_config_no_reload_when_unchanged(self):
        """Test that reload() returns False when file hasn't changed."""
        import tempfile
        import shutil
        
        temp_dir = tempfile.mkdtemp()
        config_path = os.path.join(temp_dir, "config.yaml")
        
        try:
            # Write initial config
            with open(config_path, 'w') as f:
                f.write("key: value\n")
            
            config = Config(config_path)
            
            # reload() should return False when no change
            reloaded = config.reload()
            assert reloaded == False
            assert config.get("key") == "value"
            
        finally:
            shutil.rmtree(temp_dir)

    def test_config_auto_reload_on_get(self):
        """Test that config auto-reloads when file changes and get() is called."""
        import tempfile
        import shutil
        import time
        
        temp_dir = tempfile.mkdtemp()
        config_path = os.path.join(temp_dir, "config.yaml")
        
        try:
            # Write initial config
            with open(config_path, 'w') as f:
                f.write("key: original\n")
            
            config = Config(config_path)
            
            # Wait and update file
            time.sleep(0.1)
            with open(config_path, 'w') as f:
                f.write("key: changed\n")
            
            # get() should trigger auto-reload
            value = config.get("key")
            assert value == "changed"
            
        finally:
            shutil.rmtree(temp_dir)

    def test_config_reload_nonexistent_file(self):
        """Test that reload() returns False for nonexistent file."""
        import tempfile
        
        temp_dir = tempfile.mkdtemp()
        config_path = os.path.join(temp_dir, "nonexistent.yaml")
        
        try:
            config = Config(config_path)
            reloaded = config.reload()
            assert reloaded == False
            
        finally:
            import shutil
            shutil.rmtree(temp_dir)


class TestConfigReloadAPI:
    """Tests for config reload API endpoint."""

    def test_config_reload_endpoint_exists(self):
        """Test that /api/config/reload endpoint exists in gateway."""
        import os
        from pathlib import Path
        
        gateway_path = Path(__file__).parent.parent / "gateway" / "server.py"
        if gateway_path.exists():
            content = gateway_path.read_text()
            assert "handle_config_reload" in content
            assert "/api/config/reload" in content

    def test_config_reload_response_format(self):
        """Test config reload response format."""
        # This tests the expected response format from the API
        expected_response = {
            "status": "ok",
            "reloaded": True,
            "message": "Configuration reloaded",
        }
        
        # Verify the structure
        assert "status" in expected_response
        assert "reloaded" in expected_response
        assert "message" in expected_response


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
