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
        Path.home() / ".efp" / "config.yaml",  # User config directory
        Path(__file__).parent / "config.yaml",  # Project directory
    ]

    PROJECT_EXAMPLE = Path(__file__).parent.parent / 'config.yaml.example'

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
        import shutil
        shutil.copy(self.PROJECT_EXAMPLE, self.DEFAULT_PATHS[0])
        return self.DEFAULT_PATHS[0]

    def load(self) -> None:
        """Load configuration from YAML file."""
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = self._yaml.load(f) or {}
            self._last_modified = self.config_path.stat().st_mtime
        else:
            self._config = {}
        
        # Decrypt sensitive fields
        self._decrypt_sensitive_fields(self._config)
    
    def _is_mapping(self, obj: Any) -> bool:
        """Check if obj is a mapping (dict or CommentedMap)."""
        from collections.abc import Mapping
        return isinstance(obj, Mapping)
    
    def _is_sequence(self, obj: Any) -> bool:
        """Check if obj is a sequence (list or CommentedSeq)."""
        from collections.abc import Sequence
        return isinstance(obj, Sequence) and not isinstance(
            obj, (str, bytes, bytearray, memoryview)
        )
    
    def _get_encryption_key(self) -> Optional[str]:
        """Get encryption key from environment variable."""
        import os
        return os.environ.get("EFP_CONFIG_KEY")
    
    def _encrypt_value(self, value: str) -> str:
        """Encrypt a value using Fernet (AES-CBC + HMAC)."""
        import base64
        import hashlib
        from cryptography.fernet import Fernet
        
        key = self._get_encryption_key()
        if not key:
            return value
        
        # Generate key from the configured key (must be 32 bytes for Fernet)
        key_bytes = hashlib.sha256(key.encode()).digest()
        f = Fernet(base64.urlsafe_b64encode(key_bytes))
        
        # Fernet.encrypt returns urlsafe-base64 directly, no extra wrap needed
        encrypted = f.encrypt(value.encode())
        return f"ENC:{encrypted.decode()}"
    
    def _decrypt_value(self, value: str) -> str:
        """Decrypt a value using Fernet (AES-CBC + HMAC)."""
        import logging
        import base64
        from cryptography.fernet import Fernet
        
        if not value.startswith("ENC:"):
            return value
        
        key = self._get_encryption_key()
        if not key:
            # Fail fast: encrypted config values require EFP_CONFIG_KEY
            raise RuntimeError(
                "Found ENC: value in configuration but EFP_CONFIG_KEY is not set. "
                "Set EFP_CONFIG_KEY to the correct encryption key before starting the application."
            )
        
        try:
            import hashlib
            key_bytes = hashlib.sha256(key.encode()).digest()
            f = Fernet(base64.urlsafe_b64encode(key_bytes))
            
            # Fernet returns base64-encoded token, just remove prefix and decode
            decrypted = f.decrypt(value[4:].encode())
            return decrypted.decode()
        except Exception as e:
            logging.getLogger(__name__).error(
                f"Failed to decrypt config value. Check EFP_CONFIG_KEY and configuration file: {e}",
                exc_info=True,
            )
            raise RuntimeError(
                "Failed to decrypt an encrypted configuration value. "
                "Ensure EFP_CONFIG_KEY is correct and the configuration file contains valid encrypted values."
            ) from e
    
    SENSITIVE_FIELDS = {"api_key", "password", "token", "api_token", "secret"}
    
    def _encrypt_sensitive_fields(self, obj: Any) -> None:
        """Recursively encrypt sensitive fields in config."""
        if self._is_mapping(obj):
            for key, value in obj.items():
                # Skip encryption for env var placeholders or already encrypted values
                if key in self.SENSITIVE_FIELDS and isinstance(value, str) and value and not value.startswith("ENC:") and not value.startswith("${"):
                    obj[key] = self._encrypt_value(value)
                elif self._is_mapping(value) or self._is_sequence(value):
                    self._encrypt_sensitive_fields(value)
        elif self._is_sequence(obj):
            for item in obj:
                if self._is_mapping(item) or self._is_sequence(item):
                    self._encrypt_sensitive_fields(item)
    
    def _decrypt_sensitive_fields(self, obj: Any) -> None:
        """Recursively decrypt sensitive fields in config."""
        if self._is_mapping(obj):
            for key, value in obj.items():
                if key in self.SENSITIVE_FIELDS and isinstance(value, str) and value.startswith("ENC:"):
                    obj[key] = self._decrypt_value(value)
                elif self._is_mapping(value) or self._is_sequence(value):
                    self._decrypt_sensitive_fields(value)
        elif self._is_sequence(obj):
            for item in obj:
                if self._is_mapping(item) or self._is_sequence(item):
                    self._decrypt_sensitive_fields(item)
    
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
    
    def get_jira_instances(self) -> List[Dict[str, Any]]:
        """Get Jira instances as a list (supports both old and new format)."""
        jira_config = self.jira
        instances = jira_config.get("instances", [])
        
        # Backward compatibility: if instances is empty but url exists, convert old format
        if not instances and jira_config.get("url"):
            instances = [{
                "name": "Default",
                "url": jira_config.get("url", ""),
                "project": jira_config.get("project", ""),
                "username": jira_config.get("username", ""),
                "password": jira_config.get("password", ""),
                "token": jira_config.get("token", ""),
                "api_version": jira_config.get("api_version", "3"),
                "timeout": jira_config.get("timeout", 30.0),
            }]
        
        return instances
    
    def find_jira_instance(self, url: str = None, name: str = None) -> Optional[Dict[str, Any]]:
        """Find Jira instance by URL or name."""
        instances = self.get_jira_instances()
        
        if not instances:
            return None
        
        # Match by name first
        if name:
            for inst in instances:
                if inst.get("name", "").lower() == name.lower():
                    return inst
        
        # Match by URL
        if url:
            for inst in instances:
                inst_url = inst.get("url", "")
                if inst_url and url.startswith(inst_url):
                    return inst
        
        # Return first instance as default
        return instances[0] if instances else None

    @property
    def confluence(self) -> Dict[str, Any]:
        """Get Confluence configuration."""
        return self._config.get("confluence", {})
    
    def get_confluence_instances(self) -> List[Dict[str, Any]]:
        """Get Confluence instances as a list (supports both old and new format)."""
        confluence_config = self.confluence
        instances = confluence_config.get("instances", [])
        
        # Backward compatibility: if instances is empty but url exists, convert old format
        if not instances and confluence_config.get("url"):
            instances = [{
                "name": "Default",
                "url": confluence_config.get("url", ""),
                "username": confluence_config.get("username", ""),
                "password": confluence_config.get("password", ""),
                "token": confluence_config.get("token", ""),
                "space": confluence_config.get("space", ""),
            }]
        
        return instances
    
    def find_confluence_instance(self, url: str = None, name: str = None) -> Optional[Dict[str, Any]]:
        """Find Confluence instance by URL or name."""
        instances = self.get_confluence_instances()
        
        if not instances:
            return None
        
        # Match by name first
        if name:
            for inst in instances:
                if inst.get("name", "").lower() == name.lower():
                    return inst
        
        # Match by URL
        if url:
            for inst in instances:
                inst_url = inst.get("url", "")
                if inst_url and url.startswith(inst_url):
                    return inst
        
        # Return first instance as default
        return instances[0] if instances else None
    
    @property
    def debug(self) -> Dict[str, Any]:
        """Get debug configuration."""
        return self._config.get("debug", {})
    
    @property
    def proxy(self) -> Dict[str, Any]:
        """Get proxy configuration."""
        return self._config.get("proxy", {})
    
    def apply_proxy(self) -> None:
        """Apply proxy settings to os.environ."""
        proxy_config = self.proxy
        if proxy_config.get("enabled") and proxy_config.get("url"):
            url = proxy_config.get("url", "")
            
            # Add username:password if provided
            username = proxy_config.get("username")
            password = proxy_config.get("password")
            if username and password:
                # Parse existing URL and insert credentials
                from urllib.parse import urlparse, urlunparse
                parsed = urlparse(url)
                # Insert credentials into netloc
                netloc = f"{username}:{password}@{parsed.hostname}"
                if parsed.port:
                    netloc += f":{parsed.port}"
                url = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
            
            os.environ["http_proxy"] = url
            os.environ["https_proxy"] = url
            os.environ["HTTP_PROXY"] = url
            os.environ["HTTPS_PROXY"] = url
            # Handle no_proxy for internal addresses
            no_proxy = proxy_config.get("no_proxy", "localhost,127.0.0.1")
            os.environ["no_proxy"] = no_proxy
            os.environ["NO_PROXY"] = no_proxy
        elif "proxy" in self._config:
            # Only clear if proxy section exists but is disabled
            # Don't clear inherited env vars when proxy section is absent
            for var in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"]:
                os.environ.pop(var, None)
    
    @property
    def heartbeat(self) -> Dict[str, Any]:
        """Get heartbeat configuration."""
        return self._config.get("heartbeat", {})


# Global config instance
config = Config()
