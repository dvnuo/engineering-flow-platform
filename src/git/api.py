"""
Git Integration - Single source of truth for Git operations.
"""

import asyncio
import logging
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

from src.config import config, service_reload_manager
from src.workspace_defaults import DEFAULT_RUNTIME_WORKSPACE

logger = logging.getLogger(__name__)


class GitClient:
    """Git client for repository operations."""

    def __init__(self, workspace: str = None):
        self.workspace = workspace or str(DEFAULT_RUNTIME_WORKSPACE)

    async def run(self, args: list, cwd: str = None, env: dict = None) -> str:
        """Run a git command and return output."""
        try:
            process_env = None
            if env:
                process_env = os.environ.copy()
                process_env.update(env)

            result = await asyncio.create_subprocess_exec(
                "git",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd or self.workspace,
                env=process_env,
            )
            stdout, _ = await result.communicate()
            output = stdout.decode("utf-8").strip()
            if result.returncode != 0:
                return f"Error: {output or 'git command failed'}"
            return output
        except Exception as e:
            return f"Error: {e}"

    async def status(self, cwd: str = None) -> str:
        """Get git status."""
        return await self.run(["status"], cwd)

    async def commit(self, message: str, cwd: str = None) -> str:
        """Create a commit with message."""
        return await self.run(["commit", "-m", message], cwd)

    async def push(self, cwd: str = None) -> str:
        """Push to remote."""
        cwd = cwd or self.workspace
        await self._ensure_https_origin(cwd)
        askpass_env, cleanup = self._build_askpass_env()
        try:
            result = await self.run(["push"], cwd, env=askpass_env)
            return self._with_token_hint_if_needed(result)
        finally:
            cleanup()

    async def pull(self, cwd: str = None) -> str:
        """Pull from remote."""
        cwd = cwd or self.workspace
        await self._ensure_https_origin(cwd)
        askpass_env, cleanup = self._build_askpass_env()
        try:
            result = await self.run(["pull"], cwd, env=askpass_env)
            return self._with_token_hint_if_needed(result)
        finally:
            cleanup()

    def normalize_repo_url(self, repo_url: str) -> str:
        """Normalize supported git URLs to clean HTTPS URLs."""
        repo_url = (repo_url or "").strip()
        if not repo_url:
            return repo_url

        scp_like = re.match(r"^git@([^:]+):(.+)$", repo_url)
        if scp_like:
            host = scp_like.group(1)
            path = scp_like.group(2).lstrip("/")
            return f"https://{host}/{path}"

        parsed = urlsplit(repo_url)
        host = parsed.hostname
        if host and parsed.port:
            host = f"{host}:{parsed.port}"

        if parsed.scheme == "ssh" and host:
            path = parsed.path.lstrip("/")
            return urlunsplit(("https", host, f"/{path}", "", ""))

        if parsed.scheme in {"http", "https"} and host:
            path = parsed.path
            return urlunsplit(("https", host, path, "", ""))

        return repo_url

    async def clone(self, repo_url: str, target_dir: str = None) -> str:
        """Clone a repository over HTTPS with optional askpass auth."""
        normalized_repo_url = self.normalize_repo_url(repo_url)

        if not target_dir:
            repo_name = normalized_repo_url.rstrip("/").split("/")[-1].replace(".git", "")
            target_dir = os.path.join(self.workspace, repo_name)

        target = os.path.join(self.workspace, target_dir.split("/")[-1].replace(".git", ""))
        os.makedirs(self.workspace, exist_ok=True)

        askpass_env, cleanup = self._build_askpass_env()
        try:
            output = await self.run(["clone", normalized_repo_url, target], cwd=self.workspace, env=askpass_env)
            if "Error:" in output:
                if "Could not resolve host" in output:
                    return f"Error: Could not resolve host. Please check the repository URL: {normalized_repo_url}"
                if "Repository not found" in output:
                    return f"Error: Repository not found. Please check the URL: {normalized_repo_url}"
                return self._with_token_hint_if_needed(output)

            return output if output else f"Successfully cloned to {target}"
        except Exception as e:
            return f"Error: {e}"
        finally:
            cleanup()

    def _get_github_token(self) -> str:
        github_config = config.get("github", {}) or {}
        if not isinstance(github_config, dict):
            return ""
        if not bool(github_config.get("enabled")):
            return ""
        return str(github_config.get("api_token") or "").strip()

    def _build_askpass_env(self) -> tuple[Optional[dict], Callable[[], None]]:
        token = self._get_github_token()
        if not token:
            return None, lambda: None

        askpass_fd, askpass_path = tempfile.mkstemp(prefix="efp_git_askpass_")
        script = """#!/bin/sh
prompt="$1"
case "$prompt" in
  *Username*|*username*) printf '%s\n' 'x-access-token' ;;
  *) printf '%s\n' "$EFP_GITHUB_TOKEN" ;;
esac
"""
        with os.fdopen(askpass_fd, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(askpass_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        env = {
            "GIT_ASKPASS": askpass_path,
            "GIT_TERMINAL_PROMPT": "0",
            "EFP_GITHUB_TOKEN": token,
        }

        def cleanup() -> None:
            try:
                os.remove(askpass_path)
            except FileNotFoundError:
                pass

        return env, cleanup

    async def _ensure_https_origin(self, cwd: str) -> None:
        origin = await self.run(["remote", "get-url", "origin"], cwd=cwd)
        if origin.startswith("Error:"):
            return

        normalized = self.normalize_repo_url(origin)
        if normalized != origin:
            await self.run(["remote", "set-url", "origin", normalized], cwd=cwd)

    def _with_token_hint_if_needed(self, output: str) -> str:
        if output.startswith("Error:") and not self._get_github_token():
            lowered = output.lower()
            if any(msg in lowered for msg in ["repository not found", "authentication", "permission denied", "could not read username"]):
                return (
                    f"{output}\n"
                    "Configure github.api_token for private GitHub repository access over HTTPS."
                )
        return output


# Standalone functions for backward compatibility
async def _run_git_command(args: list, cwd: str = None) -> str:
    """Run a git command and return output."""
    client = GitClient(cwd)
    return await client.run(args)


async def setup_git_user() -> bool:
    """Setup git user from config."""
    git_config = config.get("git", {})
    user_name = git_config.get("user", {}).get("name", "")
    user_email = git_config.get("user", {}).get("email", "")

    if not user_name or not user_email:
        return False

    await _run_git_command(["config", "--global", "user.name", user_name])
    await _run_git_command(["config", "--global", "user.email", user_email])

    logger.info(f"Git user configured: {user_name} <{user_email}>")
    return True


def setup_git_user_sync() -> bool:
    """Setup git user from config (sync version for config reload hooks)."""
    git_config = config.get("git", {}) or {}
    user = git_config.get("user", {}) or {}
    user_name = str(user.get("name") or "").strip()
    user_email = str(user.get("email") or "").strip()

    if not user_name or not user_email:
        return False

    try:
        name_result = subprocess.run(
            ["git", "config", "--global", "user.name", user_name],
            check=False,
            capture_output=True,
            text=True,
        )
        email_result = subprocess.run(
            ["git", "config", "--global", "user.email", user_email],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        logger.warning("Failed to configure git user: %s", exc)
        return False

    if name_result.returncode != 0 or email_result.returncode != 0:
        logger.warning(
            "Failed to configure git user: name_rc=%s email_rc=%s",
            name_result.returncode,
            email_result.returncode,
        )
        return False

    logger.info("Git user configured: %s <%s>", user_name, user_email)
    return True


def reinit_git_config() -> None:
    """Reload hook for git-related config updates."""
    setup_git_user_sync()


service_reload_manager.register("git", reinit_git_config)


__all__ = ["GitClient", "setup_git_user", "setup_git_user_sync", "reinit_git_config"]
