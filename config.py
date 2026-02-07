"""Configuration loader for Engineering Flow Platform."""

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class Config:
    """Configuration management.
    
    Searches for config.yaml in the following order:
    1. Project directory (same directory as this file)
    2. ~/.engineering-flow-platform/config.yaml
    """
    
    DEFAULT_PATHS = [
        Path(__file__).parent / "config.yaml",  # Project directory
        Path.home() / ".engineering-flow-platform" / "config.yaml",  # User config directory
    ]

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            # Find first existing config file
            self.config_path = self._find_config()
        else:
            self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self._last_modified: float = 0
        self.load()

    def _find_config(self) -> Path:
        """Find the first existing config file from default paths."""
        for path in self.DEFAULT_PATHS:
            if path.exists():
                return path
        # Return the primary path even if it doesn't exist
        return self.DEFAULT_PATHS[0]

    def load(self) -> None:
        """Load configuration from YAML file."""
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
            self._last_modified = self.config_path.stat().st_mtime
        else:
            self._config = {}
    
    @property
    def config_source(self) -> str:
        """Return the path to the loaded config file."""
        return str(self.config_path)

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
        """Get a configuration value by key (supports dot notation).
        
        Automatically reloads config if file has been modified.
        """
        # Auto-reload if file has been modified
        self.reload()
        
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
    
    @property
    def debug(self) -> Dict[str, Any]:
        """Get debug configuration."""
        return self._config.get("debug", {})
    
    @property
    def heartbeat(self) -> Dict[str, Any]:
        """Get heartbeat configuration."""
        return self._config.get("heartbeat", {})


# Global config instance
config = Config()
