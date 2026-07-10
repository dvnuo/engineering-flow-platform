import asyncio

import pytest

import src.git as git_module
from src.git.api import GitClient, setup_git_user_sync


@pytest.mark.parametrize(
    "input_url,expected",
    [
        ("git@github.com:owner/repo.git", "https://github.com/owner/repo.git"),
        ("ssh://git@github.com/owner/repo", "https://github.com/owner/repo"),
        ("https://user:token@github.com/owner/repo.git", "https://github.com/owner/repo.git"),
        ("http://github.com/owner/repo.git", "https://github.com/owner/repo.git"),
        (
            "http://user:token@github.company.com:8443/owner/repo.git",
            "https://github.company.com:8443/owner/repo.git",
        ),
        (
            "http://github.company.com:8443/owner/repo.git",
            "https://github.company.com:8443/owner/repo.git",
        ),
        ("ssh://git@github.company.com:8443/owner/repo.git", "https://github.company.com:8443/owner/repo.git"),
        ("https://github.company.com:8443/owner/repo.git", "https://github.company.com:8443/owner/repo.git"),
        (
            "https://user:token@github.company.com:8443/owner/repo.git",
            "https://github.company.com:8443/owner/repo.git",
        ),
    ],
)
def test_normalize_repo_url(input_url, expected):
    client = GitClient(workspace=".")
    assert client.normalize_repo_url(input_url) == expected


@pytest.mark.asyncio
async def test_git_run_returns_error_when_return_code_non_zero(monkeypatch):
    class Proc:
        returncode = 128

        async def communicate(self):
            return b"fatal: something bad", None

    async def fake_create_subprocess_exec(*args, **kwargs):
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    client = GitClient(workspace=".")
    result = await client.run(["status"], cwd=".")
    assert result == "Error: fatal: something bad"


@pytest.mark.asyncio
async def test_push_and_pull_convert_origin_to_https_before_execution(monkeypatch):
    class TrackingClient(GitClient):
        def __init__(self):
            super().__init__(workspace=".")
            self.calls = []

        def _build_askpass_env(self):
            return None, lambda: None

        async def run(self, args, cwd=None, env=None):
            self.calls.append(args)
            if args[:3] == ["remote", "get-url", "origin"]:
                return "git@github.com:owner/repo.git"
            return "ok"

    client = TrackingClient()
    await client.push(cwd="/tmp/repo")
    await client.pull(cwd="/tmp/repo")

    assert ["remote", "get-url", "origin"] in client.calls
    assert ["remote", "set-url", "origin", "https://github.com/owner/repo.git"] in client.calls
    assert ["push"] in client.calls
    assert ["pull"] in client.calls


def test_git_module_public_surface_includes_expected_tools():
    assert callable(git_module.git_status)
    assert callable(git_module.git_commit)
    assert callable(git_module.git_push)
    assert callable(git_module.git_clone)
    assert callable(git_module.setup_git_user_sync)


def test_setup_git_user_sync_uses_profile_config(monkeypatch):
    calls = []

    def fake_get(key, default=None):
        if key == "git":
            return {"user": {"name": "Bot", "email": "bot@example.com"}}
        return default

    class FakeCompleted:
        returncode = 0

    def fake_run(cmd, check=False, capture_output=False, text=False):
        calls.append((cmd, check, capture_output, text))
        return FakeCompleted()

    monkeypatch.setattr("src.git.api.config.get", fake_get)
    monkeypatch.setattr("src.git.api.subprocess.run", fake_run)

    assert setup_git_user_sync() is True
    assert (["git", "config", "--global", "user.name", "Bot"], False, True, True) in calls
    assert (["git", "config", "--global", "user.email", "bot@example.com"], False, True, True) in calls


def test_setup_git_user_sync_returns_false_on_git_config_failure(monkeypatch):
    class FakeCompleted:
        returncode = 1

    def fake_get(key, default=None):
        if key == "git":
            return {"user": {"name": "Bot", "email": "bot@example.com"}}
        return default

    def fake_run(cmd, check=False, capture_output=False, text=False):
        return FakeCompleted()

    monkeypatch.setattr("src.git.api.config.get", fake_get)
    monkeypatch.setattr("src.git.api.subprocess.run", fake_run)

    assert setup_git_user_sync() is False


def test_git_client_askpass_respects_github_enabled_false(monkeypatch):
    def fake_get(key, default=None):
        if key == "github":
            return {"enabled": False, "api_token": "ghp_should_not_be_used", "base_url": ""}
        return default

    monkeypatch.setattr("src.git.api.config.get", fake_get)

    client = GitClient(workspace=".")
    env, cleanup = client._build_askpass_env()
    try:
        assert env is None
    finally:
        cleanup()


def test_git_client_askpass_uses_token_only_when_enabled(monkeypatch):
    token = "ghp_test_token"

    def fake_get(key, default=None):
        if key == "github":
            return {"enabled": True, "api_token": token, "base_url": ""}
        return default

    monkeypatch.setattr("src.git.api.config.get", fake_get)

    client = GitClient(workspace=".")
    env, cleanup = client._build_askpass_env()
    try:
        assert env is not None
        assert env["EFP_GITHUB_TOKEN"] == token
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ASKPASS"]
    finally:
        cleanup()
