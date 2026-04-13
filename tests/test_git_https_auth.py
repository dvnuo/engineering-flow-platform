import asyncio

import pytest

import src.git as git_module
from src.git.api import GitClient
from src.gateway.webchat import _remove_legacy_ssh_config


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


def test_remove_legacy_ssh_config_removes_top_level_ssh():
    config = {
        "github": {"enabled": True},
        "ssh": {"enabled": True, "private_key_path": "/tmp/id_rsa"},
    }

    _remove_legacy_ssh_config(config)

    assert "ssh" not in config


def test_git_module_public_surface_includes_expected_tools():
    assert callable(git_module.git_status)
    assert callable(git_module.git_commit)
    assert callable(git_module.git_push)
    assert callable(git_module.git_clone)
