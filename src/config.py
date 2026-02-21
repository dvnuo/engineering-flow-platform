"""Configuration loader for Engineering Flow Platform."""

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ruamel.yaml import YAML


logger = logging.getLogger(__name__)

# Module-level YAML instance for reuse
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


class ServiceReloadManager:
    """Manager for services that need to be reinitialized when config changes."""
    
    def __init__(self):
        self._services: Dict[str, Callable[[], None]] = {}
    
    def register(self, name: str, reinit_func: Callable[[], None]) -> None:
        """Register a service for reinitialization on config change.
        
        Args:
            name: Service name (e.g., 'llm', 'jira', 'confluence')
            reinit_func: Function to call to reinitialize the service
        """
        self._services[name] = reinit_func
        logger.debug(f"Registered service for config reload: {name}")
    
    def unregister(self, name: str) -> None:
        """Unregister a service."""
        if name in self._services:
            del self._services[name]
    
    def reload_all(self, changed_sections: List[str]) -> Dict[str, bool]:
        """Reload all registered services that match changed config sections.
        
        Args:
            changed_sections: List of config sections that changed (e.g., ['llm', 'jira'])
        
        Returns:
            Dict mapping service names to reload success status
        """
        results = {}
        
        # Map config sections to services
        section_to_services = {
            'llm': ['llm'],
            'jira': ['jira'],
            'confluence': ['confluence'],
            'github': ['github'],
            'session': ['session'],
        }
        
        for section in changed_sections:
            services = section_to_services.get(section, [])
            for service_name in services:
                if service_name in self._services:
                    try:
                        logger.info(f"Reloading service: {service_name}")
                        self._services[service_name]()
                        results[service_name] = True
                    except Exception as e:
                        logger.error(f"Failed to reload service {service_name}: {e}")
                        results[service_name] = False
        
        return results


# Global service reload manager
service_reload_manager = ServiceReloadManager()


class Config:
    """Configuration management.
    
    Searches for config.yaml in the following order:
    1. Project directory (same directory as this file)
    2. ~/.efp/config.yaml
    """
    
    DEFAULT_PATHS = [
        Path(__file__).parent.parent / "config.yaml",  # Project directory (same as webchat.py)
        Path.home() / ".efp" / "config.yaml",  # User config directory
    ]

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            # Find first existing config file
            self.config_path = self._find_config()
        else:
            self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self._last_modified: float = 0
        self._yaml = _yaml  # Use module-level instance
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
                self._config = self._yaml.load(f) or {}
            self._last_modified = self.config_path.stat().st_mtime
        else:
            self._config = {}
    
    @property
    def config_source(self) -> str:
        """Return the path to the loaded config file."""
        return str(self.config_path)

    def reload(self, changed_sections: Optional[List[str]] = None) -> bool:
        """Reload configuration from file and optionally notify services.
        
        Args:
            changed_sections: Optional list of config sections that changed.
                             If provided, registered services will be reinitialized.
        
        Returns:
            True if config was reloaded, False otherwise.
        """
        if not self.config_path.exists():
            return False
        
        try:
            current_mtime = self.config_path.stat().st_mtime
            # Force reload if changed_sections is provided (user explicitly saved config)
            # Otherwise only reload if file was modified
            if changed_sections or current_mtime > self._last_modified:
                self.load()
                # Notify registered services if sections are specified
                if changed_sections:
                    results = service_reload_manager.reload_all(changed_sections)
                    for service, success in results.items():
                        if success:
                            logger.info(f"Service reloaded: {service}")
                        else:
                            logger.warning(f"Service reload failed: {service}")
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
