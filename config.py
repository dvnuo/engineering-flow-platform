"""Configuration loader for OpenClaw Mini."""

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class EnvVarLoader(yaml.SafeLoader):
    """YAML loader that expands environment variables like ${VAR_NAME}."""
    pass


def _env_var_constructor(loader, node):
    """Process ${VAR_NAME} environment variables."""
    value = node.value
    # Match ${VAR_NAME} pattern
    pattern = r'\$\{([^}]+)\}'
    
    def replace(match):
        var_name = match.group(1)
        # Support default value like ${VAR:-default}
        if ':-' in var_name:
            actual_var, default = var_name.split(':-', 1)
            return os.environ.get(actual_var, default)
        return os.environ.get(var_name, match.group(0))
    
    return re.sub(pattern, replace, value)


# Register the constructor for strings
EnvVarLoader.add_constructor('tag:yaml.org,2002:str', _env_var_constructor)


class Config:
    """Configuration management."""

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self._last_modified: float = 0
        self.load()

    def load(self) -> None:
        """Load configuration from YAML file with environment variable expansion."""
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = yaml.load(f, Loader=EnvVarLoader) or {}
            self._last_modified = self.config_path.stat().st_mtime
        else:
            self._config = {}

    def reload(self) -> bool:
        """Reload configuration from file."""
        if not self.config_path.exists():
            return False
        
        try:
            current_mtime = self.config_path.stat().st_mtime
            if current_mtime > self._last_modified:
                self.load()
                return True
        except Exception:
            pass
        return False

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key (supports dot notation)."""
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    @property
    def discord(self) -> Dict[str, Any]:
        """Get Discord configuration."""
        return self._config.get("discord", {})

    @property
    def llm(self) -> Dict[str, Any]:
        """Get LLM configuration."""
        return self._config.get("llm", {})

    @property
    def session(self) -> Dict[str, Any]:
        """Get session configuration."""
        return self._config.get("session", {})

    @property
    def server(self) -> Dict[str, Any]:
        """Get server configuration."""
        return self._config.get("server", {})

    @property
    def jira(self) -> Dict[str, Any]:
        """Get Jira configuration."""
        return self._config.get("jira", {})

    @property
    def confluence(self) -> Dict[str, Any]:
        """Get Confluence configuration."""
        return self._config.get("confluence", {})


# Global config instance
config = Config()
