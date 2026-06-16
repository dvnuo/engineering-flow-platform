"""Configuration loader for Engineering Flow Platform."""

from __future__ import annotations

import logging
import math
import os
import time
import copy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


logger = logging.getLogger(__name__)

# Module-level YAML instance for reuse
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def _home_path() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


DEFAULT_LLM_MODEL = "gpt-5.4"
DEFAULT_LLM_TEMPERATURE = 0.7

PORTAL_MANAGED_RUNTIME_FIELDS = frozenset(
    {
        "enabled_tools",
        "disabled_tools",
        "tool_permissions",
        "max_iterations",
        "doom_loop_threshold",
        "max_context_parts",
        "max_context_chars",
        "max_context_tokens",
        "context_reserve_chars",
        "context_reserve_tokens",
        "compaction_auto",
        "compaction_prune",
        "compaction_tail_turns",
        "compaction_preserve_recent_chars",
        "compaction_preserve_recent_tokens",
        "compaction_reserved_chars",
        "compaction_tool_output_max_chars",
        "compaction_prune_min_chars",
        "compaction_prune_protect_chars",
        "enable_compaction_summarizer",
        "enable_context_overflow_retry",
        "enable_session_revert_snapshots",
        "skill_directories",
        "active_skills",
        "command_directories",
        "enable_command_expansion",
        "system_prompt_texts",
        "system_prompt_paths",
        "include_default_system_prompt",
        "include_environment_context",
        "max_system_prompt_chars",
        "include_runtime_reminders",
        "instruction_texts",
        "instruction_paths",
        "include_default_instructions",
        "attach_read_instructions",
        "max_instruction_chars",
        "include_skill_sidecar_content",
        "max_skill_sidecar_chars",
        "max_command_chars",
        "resolve_prompt_references",
        "max_prompt_reference_chars",
        "max_prompt_directory_entries",
        "runtime_mode",
        "enable_plan_tool",
        "plan_mode_read_only",
        "enable_question_tool",
        "enable_lsp_tool",
        "inject_background_task_results",
        "model_aware_tool_selection",
        "structured_output_schema",
        "tool_output_max_lines",
        "tool_output_max_bytes",
        "tool_output_truncation_direction",
        "archive_truncated_tool_outputs",
        "tool_output_dir",
        "emit_llm_stream_events",
        "track_usage",
    }
)

RUNTIME_PROFILE_EXTERNAL_CLI_INSTRUCTIONS = [
    (
        "Use bash for external CLI tools configured by the runtime profile: "
        "jira, confluence, gh, aws, and git."
    ),
    (
        "For jira and confluence, always pass --json. Before complex commands, "
        "inspect commands/schema/help llm; prefer --dry-run for writes, and use "
        "--yes only when a destructive action was explicitly confirmed."
    ),
    "Use gh for GitHub issues, pull requests, and api calls; use aws for AWS operations; use git for clone, fetch, push, and status.",
    (
        "Credentials were applied by the runtime profile through the real CLIs; "
        "if jira or confluence returns auth_failed, aws returns an auth error, or gh/git authentication fails, "
        "report a runtime profile configuration problem."
    ),
]

_ATLASSIAN_INSTANCE_URL_FIELDS = ("url", "base_url", "baseUrl", "uri")


def _first_atlassian_instance_url(value: Dict[str, Any]) -> str:
    if not isinstance(value, dict):
        return ""
    for field in _ATLASSIAN_INSTANCE_URL_FIELDS:
        text = str(value.get(field) or "").strip()
        if text:
            return text.rstrip("/")
    return ""


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
            'git': ['git'],
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


def _should_inject_runtime_profile_external_cli_instructions(overlay: Dict[str, Any]) -> bool:
    if not isinstance(overlay, dict) or "instruction_texts" in overlay:
        return False
    return (
        _has_atlassian_profile_instances(overlay.get("jira"))
        or _has_atlassian_profile_instances(overlay.get("confluence"))
        or _has_github_profile_token(overlay.get("github"))
        or _has_aws_profile_config(overlay.get("aws"))
        or _has_git_profile_user(overlay.get("git"))
    )


def _has_atlassian_profile_instances(section: Any) -> bool:
    if not isinstance(section, dict) or section.get("enabled") is False:
        return False
    instances = section.get("instances")
    if not isinstance(instances, list):
        return False
    for instance in instances:
        if not isinstance(instance, dict) or instance.get("enabled") is False:
            continue
        if _first_atlassian_instance_url(instance):
            return True
    return False


def _has_github_profile_token(section: Any) -> bool:
    if not isinstance(section, dict) or section.get("enabled") is False:
        return False
    return bool(str(section.get("api_token") or section.get("token") or section.get("access_token") or "").strip())


def _has_aws_profile_config(section: Any) -> bool:
    if not isinstance(section, dict) or section.get("enabled") is False:
        return False
    domain = str(section.get("domain") or "").strip()
    username = str(section.get("username") or "").strip()
    password = str(section.get("password") or "").strip()
    return bool(domain and username and password)


def _has_git_profile_user(section: Any) -> bool:
    user = section.get("user") if isinstance(section, dict) else None
    if not isinstance(user, dict):
        return False
    return bool(str(user.get("name") or "").strip() and str(user.get("email") or "").strip())


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
    PROXY_ENV_VARS = (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    )

    PROJECT_EXAMPLE = Path(__file__).parent.parent / 'config.yaml.example'
    MANAGED_OVERLAY_SECTIONS = {
        "llm",
        "proxy",
        "jira",
        "confluence",
        "github",
        "aws",
        "git",
        "debug",
        *PORTAL_MANAGED_RUNTIME_FIELDS,
    }
    PORTAL_MANAGED_FIELD_TREE = {
        **{field: True for field in sorted(PORTAL_MANAGED_RUNTIME_FIELDS)},
        # Keep hidden/deprecated Portal LLM fields in this field tree.
        # Portal may stop rendering temperature/tools/response_flow controls, but
        # set_managed_overlay() must still prune older Portal-managed values from
        # config.yaml when a newer sparse overlay omits them.
        "llm": {
            "provider": True,
            "model": True,
            "api_key": True,
            "reasoning_effort": True,
            "timeout_ms": True,
            "timeout_seconds": True,
            "timeout": True,
            "temperature": True,
            "reasoning_replay": True,
            "max_tokens": True,
            "tools": True,
            "context_budget": True,
            "context_projection": True,
            "response_flow": True,
            "tool_loop": True,
        },
        "proxy": {
            "enabled": True,
            "url": True,
            "username": True,
            "password": True,
            "no_proxy": True,
            "noProxy": True,
        },
        "jira": {
            "enabled": True,
            "instances": True,
            "default_instance": True,
        },
        "confluence": {
            "enabled": True,
            "instances": True,
            "default_instance": True,
        },
        "github": {
            "enabled": True,
            "api_token": True,
            "token": True,
            "access_token": True,
            "base_url": True,
            "api_base_url": True,
        },
        "aws": {
            "enabled": True,
            "domain": True,
            "username": True,
            "password": True,
        },
        "git": {
            "user": {
                "name": True,
                "email": True,
            },
        },
        "debug": {
            "enabled": True,
            "log_level": True,
        },
    }

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            # Find first existing config file
            self.config_path = self._find_config()
        else:
            self.config_path = Path(config_path)
        self.runtime_profile_path = _home_path() / ".efp" / "runtime_profile.yaml"
        self._config: Dict[str, Any] = {}
        self._base_config: Dict[str, Any] = {}
        self._managed_overlay_meta: Dict[str, Any] = {
            "runtime_profile_id": None,
            "revision": None,
        }
        self._managed_sections: List[str] = []
        self._external_config_status: Dict[str, Any] = {
            "success": True,
            "error": None,
            "operation": None,
        }
        self._last_modified: float = 0
        self._yaml = _yaml  # Use module-level instance
        self.load()

    def _find_config(self) -> Path:
        """Find the first existing config file from default paths."""
        default_paths = [
            _home_path() / ".efp" / "config.yaml",
            Path(__file__).parent / "config.yaml",
        ]
        for path in default_paths:
            if path.exists():
                return path
        # Return the primary path even if it doesn't exist
        import shutil
        target = default_paths[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.PROJECT_EXAMPLE, target)
        return target

    def load(self) -> None:
        """Load configuration from config.yaml and clean up legacy sidecar state."""
        self._base_config = self._load_yaml_document(self.config_path)
        self._last_modified = self.config_path.stat().st_mtime if self.config_path.exists() else 0
        
        # Decrypt sensitive fields
        self._decrypt_sensitive_fields(self._base_config)
        self._cleanup_legacy_runtime_profile_file()
        self._rebuild_effective_config()

    def _cleanup_legacy_runtime_profile_file(self) -> None:
        """Delete legacy runtime_profile.yaml sidecar if it exists.

        NOTE: runtime_profile.yaml is no longer an active runtime configuration
        input. Portal bootstrap remains the source of truth for managed fields.
        """
        if not self.runtime_profile_path.exists():
            return
        try:
            self.runtime_profile_path.unlink()
            logger.info("Removed legacy runtime profile sidecar: %s", self.runtime_profile_path)
        except Exception as exc:
            logger.warning(
                "Failed to remove legacy runtime profile sidecar %s: %s",
                self.runtime_profile_path,
                exc,
            )

    def _load_yaml_document(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return CommentedMap()
        with open(path, "r", encoding="utf-8") as f:
            document = self._yaml.load(f) or CommentedMap()
        return document if isinstance(document, dict) else CommentedMap()

    def _write_yaml_document(self, path: Path, document: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            self._yaml.dump(document, f)

    def _deep_merge(self, base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
        result = copy.deepcopy(base)
        self._deep_merge_into(result, overlay)
        return result

    def _deep_merge_into(self, base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in (overlay or {}).items():
            if self._is_mapping(base.get(key)) and self._is_mapping(value):
                self._deep_merge_into(base[key], value)
            else:
                base[key] = copy.deepcopy(value)
        return base

    def _prune_by_field_tree(self, target: Dict[str, Any], field_tree: Dict[str, Any]) -> None:
        if not self._is_mapping(target) or not self._is_mapping(field_tree):
            return
        for key, subtree in field_tree.items():
            if key not in target:
                continue
            if subtree is True:
                del target[key]
                continue
            current_value = target.get(key)
            if self._is_mapping(current_value):
                self._prune_by_field_tree(current_value, subtree)
                if not current_value:
                    del target[key]
            else:
                del target[key]

    def _filter_by_field_tree(self, source: Dict[str, Any], field_tree: Dict[str, Any]) -> Dict[str, Any]:
        filtered: Dict[str, Any] = {}
        if not self._is_mapping(source) or not self._is_mapping(field_tree):
            return filtered
        for key, subtree in field_tree.items():
            if key not in source:
                continue
            value = source.get(key)
            if subtree is True:
                filtered[key] = copy.deepcopy(value)
                continue
            if self._is_mapping(value):
                nested = self._filter_by_field_tree(value, subtree)
                if nested:
                    filtered[key] = nested
        return filtered

    def _filter_managed_overlay_sections(self, overlay_config: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(overlay_config, dict):
            return {}
        filtered: Dict[str, Any] = {}
        for section, value in overlay_config.items():
            if section in self.MANAGED_OVERLAY_SECTIONS:
                filtered[section] = copy.deepcopy(value)
        return self._filter_by_field_tree(filtered, self.PORTAL_MANAGED_FIELD_TREE)

    def _rebuild_effective_config(self) -> None:
        self._config = copy.deepcopy(self._base_config)
        llm_cfg = self._config.get("llm")
        if isinstance(llm_cfg, dict):
            llm_cfg.setdefault("reasoning_effort", "high")
            llm_cfg.setdefault("reasoning_replay", False)

    def load_managed_overlay(self) -> Dict[str, Any]:
        """Legacy compatibility no-op.

        Runtime no longer loads any overlay sidecar file. Managed runtime-profile
        fields are applied directly into config.yaml via set_managed_overlay().
        """
        self._managed_overlay_meta = {"runtime_profile_id": None, "revision": None}
        self._managed_sections = []
        return {}

    def _persist_runtime_config(self, config_document: Dict[str, Any]) -> None:
        encrypted = copy.deepcopy(config_document)
        self._encrypt_sensitive_fields(encrypted)
        self._write_yaml_document(self.config_path, encrypted)

    def _set_external_config_status(
        self,
        operation: Optional[str],
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        self._external_config_status = {
            "success": bool(success),
            "error": error if error else None,
            "operation": operation if operation in {"apply", "clear"} else None,
        }

    def _external_config_exc_info(self, error: str, exc: BaseException):
        return (RuntimeError, RuntimeError(error), exc.__traceback__)

    def set_managed_overlay(
        self,
        runtime_profile_id: Optional[str],
        revision: Optional[int],
        overlay_config: Dict[str, Any],
    ) -> List[str]:
        """Apply Portal-managed snapshot into config.yaml and reload."""
        previous_sections = set(self._managed_sections)
        filtered_overlay = self._filter_managed_overlay_sections(overlay_config or {})
        external_cli_overlay = copy.deepcopy(filtered_overlay)
        if _should_inject_runtime_profile_external_cli_instructions(external_cli_overlay):
            external_cli_overlay["instruction_texts"] = list(RUNTIME_PROFILE_EXTERNAL_CLI_INSTRUCTIONS)
        persisted_overlay = copy.deepcopy(external_cli_overlay)
        persisted_overlay.pop("jira", None)
        persisted_overlay.pop("confluence", None)
        new_sections = set(external_cli_overlay.keys())

        config_document = self._load_yaml_document(self.config_path)
        self._decrypt_sensitive_fields(config_document)
        self._prune_by_field_tree(config_document, self.PORTAL_MANAGED_FIELD_TREE)
        self._deep_merge_into(config_document, persisted_overlay)
        self._persist_runtime_config(config_document)

        from src.external_cli.profile_config import (
            apply_runtime_profile_external_config,
            redact_runtime_profile_external_config_error,
        )

        try:
            apply_runtime_profile_external_config(external_cli_overlay, config_path=self.config_path)
        except Exception as exc:
            error = redact_runtime_profile_external_config_error(exc, external_cli_overlay)
            self._set_external_config_status("apply", False, error)
            logger.warning(
                "Runtime profile external CLI config apply failed: %s",
                error,
                exc_info=self._external_config_exc_info(error, exc),
            )
        else:
            self._set_external_config_status("apply", True)

        self._managed_overlay_meta = {
            "runtime_profile_id": runtime_profile_id,
            "revision": revision,
        }
        self._managed_sections = sorted(new_sections)
        self._cleanup_legacy_runtime_profile_file()

        # Use union so section removals (e.g. proxy removed from overlay) still
        # trigger reload/apply side effects for the removed section.
        changed_sections = sorted(previous_sections | new_sections)
        self.reload(changed_sections=changed_sections)
        if "proxy" in changed_sections:
            if (
                "proxy" in previous_sections
                and "proxy" not in new_sections
                and "proxy" not in self._config
            ):
                self._clear_proxy_env()
            else:
                self.apply_proxy()
        return changed_sections

    def clear_managed_overlay(self) -> None:
        """Remove all Portal-managed fields from config.yaml and reload."""
        previous_sections = sorted(self._managed_sections)
        config_document = self._load_yaml_document(self.config_path)
        self._decrypt_sensitive_fields(config_document)
        self._prune_by_field_tree(config_document, self.PORTAL_MANAGED_FIELD_TREE)
        self._persist_runtime_config(config_document)

        from src.external_cli.profile_config import (
            clear_runtime_profile_external_config,
            redact_runtime_profile_external_config_error,
        )

        try:
            clear_runtime_profile_external_config(config_path=self.config_path)
        except Exception as exc:
            error = redact_runtime_profile_external_config_error(exc)
            self._set_external_config_status("clear", False, error)
            logger.warning(
                "Runtime profile external CLI config clear failed: %s",
                error,
                exc_info=self._external_config_exc_info(error, exc),
            )
        else:
            self._set_external_config_status("clear", True)

        self._managed_overlay_meta = {"runtime_profile_id": None, "revision": None}
        self._managed_sections = []
        self._cleanup_legacy_runtime_profile_file()
        self.reload(changed_sections=previous_sections)
        if "proxy" in previous_sections:
            if "proxy" in self._config:
                self.apply_proxy()
            else:
                self._clear_proxy_env()

    def get_effective_config(self) -> Dict[str, Any]:
        return copy.deepcopy(self._config)

    def get_managed_overlay_meta(self) -> Dict[str, Any]:
        return {
            "runtime_profile_id": self._managed_overlay_meta.get("runtime_profile_id"),
            "revision": self._managed_overlay_meta.get("revision"),
            "managed_sections": sorted(self._managed_sections),
        }

    def get_external_config_status(self) -> Dict[str, Any]:
        return {
            "success": bool(self._external_config_status.get("success")),
            "error": self._external_config_status.get("error"),
            "operation": self._external_config_status.get("operation"),
        }

    def save_partial_sections(self, updates: Dict[str, Any], sections: List[str]) -> List[str]:
        """Persist partial section updates into config.yaml preserving YAML structure."""
        config_document = self._load_yaml_document(self.config_path)
        self._decrypt_sensitive_fields(config_document)
        updated_sections: List[str] = []

        for section in sections:
            if section not in updates:
                continue
            if (
                section in config_document
                and self._is_mapping(config_document.get(section))
                and self._is_mapping(updates[section])
            ):
                self._deep_merge_into(config_document[section], updates[section])
            else:
                config_document[section] = copy.deepcopy(updates[section])
            updated_sections.append(section)

        self._persist_runtime_config(config_document)
        self.reload(changed_sections=updated_sections)
        return updated_sections
    
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
    
    SENSITIVE_FIELDS = {"api_key", "password", "token", "api_token", "access_token", "secret"}
    
    def _encrypt_sensitive_fields(self, obj: Any, path: tuple[str, ...] = ()) -> None:
        """Recursively encrypt sensitive fields in config."""
        if self._is_mapping(obj):
            for key, value in obj.items():
                child_path = (*path, str(key))
                if path == ("aws",) and key == "password":
                    continue
                # Skip encryption for env var placeholders or already encrypted values
                if key in self.SENSITIVE_FIELDS and isinstance(value, str) and value and not value.startswith("ENC:") and not value.startswith("${"):
                    obj[key] = self._encrypt_value(value)
                elif self._is_mapping(value) or self._is_sequence(value):
                    self._encrypt_sensitive_fields(value, child_path)
        elif self._is_sequence(obj):
            for item in obj:
                if self._is_mapping(item) or self._is_sequence(item):
                    self._encrypt_sensitive_fields(item, path)
    
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
            if (
                changed_sections
                or current_mtime > self._last_modified
            ):
                self.load()
                # Notify registered services if sections are specified
                if changed_sections:
                    results = service_reload_manager.reload_all(changed_sections)
                    for service, success in results.items():
                        if success:
                            logger.info(f"Service reloaded: {service}")
                        else:
                            logger.warning(f"Service reload failed: {service}")
                    if "proxy" in changed_sections:
                        self.apply_proxy()
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
        if not instances and _first_atlassian_instance_url(jira_config):
            instances = [{
                "name": "Default",
                "url": _first_atlassian_instance_url(jira_config),
                "project": jira_config.get("project", ""),
                "username": jira_config.get("username", ""),
                "password": jira_config.get("password", ""),
                "token": jira_config.get("token", ""),
                "api_version": jira_config.get("api_version", "3"),
                "timeout": jira_config.get("timeout", 30.0),
            }]
        
        return self._normalize_atlassian_instances(instances)
    
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
        if not instances and _first_atlassian_instance_url(confluence_config):
            instances = [{
                "name": "Default",
                "url": _first_atlassian_instance_url(confluence_config),
                "username": confluence_config.get("username", ""),
                "password": confluence_config.get("password", ""),
                "token": confluence_config.get("token", ""),
                "space": confluence_config.get("space", ""),
            }]
        
        return self._normalize_atlassian_instances(instances)

    def _normalize_atlassian_instances(self, instances: Any) -> List[Dict[str, Any]]:
        if not isinstance(instances, list):
            return []
        normalized: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw in instances:
            if not isinstance(raw, dict):
                continue
            url = _first_atlassian_instance_url(raw)
            if not url:
                continue
            item = copy.deepcopy(raw)
            item["url"] = url
            key = (str(item.get("name") or "").strip().lower(), url.lower())
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        return normalized
    
    def find_confluence_instance(
        self,
        url: str = None,
        name: str = None,
        strict: bool = False,
    ) -> Optional[Dict[str, Any]]:
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
        
        if strict:
            return None

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

    def _clear_proxy_env(self) -> None:
        for var in self.PROXY_ENV_VARS:
            os.environ.pop(var, None)
    
    def apply_proxy(self) -> None:
        """Apply proxy settings to os.environ."""
        from src.utils.proxy import no_proxy_value, proxy_url_with_credentials

        proxy_config = self.proxy
        if proxy_config.get("enabled") and proxy_config.get("url"):
            url = proxy_url_with_credentials(
                proxy_config.get("url", ""),
                proxy_config.get("username"),
                proxy_config.get("password"),
            )
            
            os.environ["http_proxy"] = url
            os.environ["https_proxy"] = url
            os.environ["HTTP_PROXY"] = url
            os.environ["HTTPS_PROXY"] = url
            os.environ["all_proxy"] = url
            os.environ["ALL_PROXY"] = url
            # Handle no_proxy for internal addresses
            no_proxy = no_proxy_value(proxy_config)
            os.environ["no_proxy"] = no_proxy
            os.environ["NO_PROXY"] = no_proxy
        elif "proxy" in self._config:
            # Only clear if proxy section exists but is disabled
            # Don't clear inherited env vars when proxy section is absent
            self._clear_proxy_env()
    
    @property
    def heartbeat(self) -> Dict[str, Any]:
        """Get heartbeat configuration."""
        return self._config.get("heartbeat", {})


DEFAULT_MODEL_LIMITS: Dict[str, Dict[str, int]] = {
    "gpt-5-mini": {
        "max_context_window_tokens": 264000,
        "max_prompt_tokens": 128000,
        "max_output_tokens": 64000,
    },
    "gpt-5.3-codex": {
        "max_context_window_tokens": 400000,
        "max_prompt_tokens": 272000,
        "max_output_tokens": 128000,
    },
    "gpt-5.4": {
        "max_context_window_tokens": 400000,
        "max_prompt_tokens": 272000,
        "max_output_tokens": 128000,
    },
    "gpt-5.4-mini": {
        "max_context_window_tokens": 400000,
        "max_prompt_tokens": 272000,
        "max_output_tokens": 128000,
    },
    "gpt-5.5": {
        "max_context_window_tokens": 400000,
        "max_prompt_tokens": 272000,
        "max_output_tokens": 128000,
    },
    "gemini-2.5-pro": {
        "max_context_window_tokens": 128_000,
        "max_prompt_tokens": 128_000,
        "max_output_tokens": 64000,
    },
    "gemini-3.5-flash": {
        "max_context_window_tokens": 128_000,
        "max_prompt_tokens": 128_000,
        "max_output_tokens": 64000,
    },
}


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def resolve_llm_temperature(explicit: Optional[Any] = None) -> float:
    source = explicit if explicit is not None else config.llm.get("temperature", DEFAULT_LLM_TEMPERATURE)
    if isinstance(source, bool):
        return float(DEFAULT_LLM_TEMPERATURE)
    if source is None:
        return float(DEFAULT_LLM_TEMPERATURE)
    if isinstance(source, str):
        source = source.strip()
        if not source:
            return float(DEFAULT_LLM_TEMPERATURE)
    try:
        parsed = float(source)
    except (TypeError, ValueError):
        return float(DEFAULT_LLM_TEMPERATURE)
    if not math.isfinite(parsed) or parsed < 0 or parsed > 2:
        return float(DEFAULT_LLM_TEMPERATURE)
    return float(parsed)


def resolve_model_limits(model: Optional[str] = None) -> Dict[str, int]:
    llm_cfg = config.llm if isinstance(config.llm, dict) else {}
    configured_model = _canonical_model_limit_key(
        model or llm_cfg.get("model") or DEFAULT_LLM_MODEL
    )
    configured_limits = llm_cfg.get("model_limits") if isinstance(llm_cfg.get("model_limits"), dict) else {}
    candidates: Dict[str, Dict[str, int]] = dict(DEFAULT_MODEL_LIMITS)
    for key, raw in configured_limits.items():
        if not isinstance(raw, dict):
            continue
        candidates[_canonical_model_limit_key(key)] = {
            "max_context_window_tokens": _safe_positive_int(raw.get("max_context_window_tokens"), 264000),
            "max_prompt_tokens": _safe_positive_int(raw.get("max_prompt_tokens"), 128000),
            "max_output_tokens": _safe_positive_int(raw.get("max_output_tokens"), _safe_positive_int(llm_cfg.get("max_tokens"), 64000)),
        }

    selected = candidates.get(configured_model, {})
    if not selected and configured_model:
        for key in sorted(candidates.keys(), key=len, reverse=True):
            if key in configured_model:
                selected = candidates[key]
                break
    if not selected:
        selected = {
            "max_context_window_tokens": 200000,
            "max_prompt_tokens": 128000,
            "max_output_tokens": _safe_positive_int(llm_cfg.get("max_tokens"), 64000),
        }
    selected = dict(selected)
    selected["max_output_tokens"] = _safe_positive_int(selected.get("max_output_tokens"), _safe_positive_int(llm_cfg.get("max_tokens"), 64000))
    selected["max_prompt_tokens"] = _safe_positive_int(selected.get("max_prompt_tokens"), 128000)
    selected["max_context_window_tokens"] = _safe_positive_int(selected.get("max_context_window_tokens"), 264000)
    return selected


def _canonical_model_limit_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "/" in text:
        text = text.split("/", 1)[1]
    return "-".join(text.split())


def resolve_output_boundary(model: Optional[str] = None) -> Dict[str, int | str]:
    llm_cfg = config.llm if isinstance(config.llm, dict) else {}
    output_cfg = llm_cfg.get("output_controller") if isinstance(llm_cfg.get("output_controller"), dict) else {}
    limits = resolve_model_limits(model)
    max_output_tokens = _safe_positive_int(limits.get("max_output_tokens"), _safe_positive_int(llm_cfg.get("max_tokens"), 64000))
    configured_chat_tokens = output_cfg.get("max_chat_output_tokens")
    default_chat_tokens = max(1, int(max_output_tokens * 0.9375))
    max_chat_output_tokens = _safe_positive_int(configured_chat_tokens, default_chat_tokens)
    max_chat_output_tokens = min(max_chat_output_tokens, max_output_tokens)
    chars_per_token = _safe_positive_int(output_cfg.get("chars_per_token_estimate"), 4)
    configured_chars = output_cfg.get("max_chat_output_chars")
    derived_chars = max_chat_output_tokens * chars_per_token
    min_reasonable_chars = int(derived_chars * 0.25)
    legacy_ignored = False
    boundary_source = "model_limits_derived"
    if configured_chars in (None, "", "null"):
        max_chat_output_chars = derived_chars
    else:
        parsed_chars = _safe_positive_int(configured_chars, derived_chars)
        allow_low = bool(output_cfg.get("allow_low_max_chat_output_chars", False))
        if parsed_chars < min_reasonable_chars and not allow_low:
            max_chat_output_chars = derived_chars
            legacy_ignored = True
            boundary_source = "model_limits_legacy_override_ignored"
        else:
            max_chat_output_chars = parsed_chars
            boundary_source = "config_override"
    strategy = str(output_cfg.get("oversized_output_strategy") or "save_and_manifest")
    return {
        "max_context_window_tokens": int(limits.get("max_context_window_tokens") or 264000),
        "max_prompt_tokens": int(limits.get("max_prompt_tokens") or 128000),
        "max_output_tokens": max_output_tokens,
        "max_chat_output_tokens": max_chat_output_tokens,
        "chars_per_token_estimate": chars_per_token,
        "max_chat_output_chars": max_chat_output_chars,
        "allow_low_max_chat_output_chars": bool(output_cfg.get("allow_low_max_chat_output_chars", False)),
        "configured_max_chat_output_chars": str(configured_chars) if configured_chars is not None else None,
        "legacy_max_chat_output_chars_ignored": legacy_ignored,
        "output_boundary_source": boundary_source,
        "oversized_output_strategy": strategy,
    }


# Global config instance
config = Config()
