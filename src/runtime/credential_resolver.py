from __future__ import annotations

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable
from urllib.parse import urlsplit

from src.config import config
from src.github.url_utils import PUBLIC_GITHUB_API_BASE, normalize_github_api_base_url

REDACTED_SECRET = "[REDACTED_GITHUB_TOKEN]"


@dataclass
class ToolCredentialEnv:
    env: Dict[str, str] = field(default_factory=dict)
    cleanup: Callable[[], None] = lambda: None
    secrets: tuple[str, ...] = ()
    sensitive_keys: frozenset[str] = frozenset()

    def redact_text(self, value: str | None) -> str:
        if value is None:
            return ""
        text = str(value)
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, REDACTED_SECRET)
        return text

    def redact_args(self, args: Iterable[str]) -> list[str]:
        return [self.redact_text(str(arg)) for arg in args]


def _github_config() -> dict:
    value = config.get("github", {}) or {}
    return value if isinstance(value, dict) else {}


def _github_enabled_and_token() -> tuple[bool, str, str]:
    github_cfg = _github_config()
    enabled = bool(github_cfg.get("enabled"))
    token = str(github_cfg.get("api_token") or "").strip()
    base_url = str(github_cfg.get("base_url") or "").strip()
    return enabled, token, base_url


def _host_from_github_api_base_url(base_url: str) -> str:
    normalized = normalize_github_api_base_url(base_url)
    parsed = urlsplit(normalized)
    return parsed.netloc or "api.github.com"


def _is_public_github_api(base_url: str) -> bool:
    normalized = normalize_github_api_base_url(base_url)
    return normalized == PUBLIC_GITHUB_API_BASE


def build_git_askpass_env() -> ToolCredentialEnv:
    enabled, token, _base_url = _github_enabled_and_token()
    if not enabled or not token:
        return ToolCredentialEnv()

    fd, askpass_path = tempfile.mkstemp(prefix="efp_git_askpass_")
    script = """#!/bin/sh
prompt="$1"
case "$prompt" in
  *Username*|*username*) printf '%s\\n' 'x-access-token' ;;
  *) printf '%s\\n' "$EFP_GITHUB_TOKEN" ;;
esac
"""
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(script)
    os.chmod(askpass_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    def cleanup() -> None:
        try:
            os.remove(askpass_path)
        except FileNotFoundError:
            pass

    return ToolCredentialEnv(
        env={
            "GIT_ASKPASS": askpass_path,
            "GIT_TERMINAL_PROMPT": "0",
            "EFP_GITHUB_TOKEN": token,
        },
        cleanup=cleanup,
        secrets=(token,),
        sensitive_keys=frozenset({"EFP_GITHUB_TOKEN"}),
    )


def build_gh_cli_env() -> ToolCredentialEnv:
    enabled, token, base_url = _github_enabled_and_token()
    if not enabled or not token:
        return ToolCredentialEnv()

    gh_config_dir = tempfile.mkdtemp(prefix="efp_gh_config_")
    env = {
        "GH_PROMPT_DISABLED": "1",
        "GH_CONFIG_DIR": gh_config_dir,
    }

    if _is_public_github_api(base_url):
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token
        sensitive = {"GH_TOKEN", "GITHUB_TOKEN"}
    else:
        host = _host_from_github_api_base_url(base_url)
        env["GH_HOST"] = host
        env["GH_ENTERPRISE_TOKEN"] = token
        env["GITHUB_ENTERPRISE_TOKEN"] = token
        sensitive = {"GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"}

    def cleanup() -> None:
        shutil.rmtree(gh_config_dir, ignore_errors=True)

    return ToolCredentialEnv(
        env=env,
        cleanup=cleanup,
        secrets=(token,),
        sensitive_keys=frozenset(sensitive),
    )


def build_env_for_command(cmd: str, args: list[str] | None = None, cwd: str | None = None) -> ToolCredentialEnv:
    _ = (args, cwd)
    normalized_cmd = (cmd or "").strip()
    if normalized_cmd == "git":
        return build_git_askpass_env()
    if normalized_cmd == "gh":
        return build_gh_cli_env()
    return ToolCredentialEnv()
