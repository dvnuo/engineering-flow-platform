import asyncio

import pytest

from src.bash_tools.api import run_command


class ToolCredentialEnv:
    def __init__(self, env=None, cleanup=lambda: None, secrets=()):
        self.env = env or {}
        self.cleanup = cleanup
        self.secrets = secrets

    def redact_text(self, value):
        text = "" if value is None else str(value)
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[REDACTED_GITHUB_TOKEN]")
        return text

    def redact_args(self, args):
        return [self.redact_text(arg) for arg in args]


@pytest.mark.asyncio
async def test_run_command_gh_injects_token_and_redacts_output(monkeypatch):
    token = "ghp_secret_token"
    captured_env = {}

    class Proc:
        returncode = 0

        async def communicate(self):
            return f"stdout {token}".encode("utf-8"), f"stderr {token}".encode("utf-8")

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(
        "src.bash_tools.api._build_credential_env",
        lambda cmd, args=None, cwd=None: ToolCredentialEnv(
            env={"GH_TOKEN": token},
            secrets=(token,),
        ),
    )

    result = await run_command("gh", ["auth", "status"])
    assert captured_env["GH_TOKEN"] == token
    assert token not in result["stdout"]
    assert token not in result["stderr"]
    assert "[REDACTED_GITHUB_TOKEN]" in result["stdout"]


@pytest.mark.asyncio
async def test_run_command_git_injects_askpass_and_meta_redacted(monkeypatch):
    token = "ghp_secret_token"
    captured_env = {}

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"ok", b"done"

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(
        "src.bash_tools.api._build_credential_env",
        lambda cmd, args=None, cwd=None: ToolCredentialEnv(
            env={
                "GIT_ASKPASS": "/tmp/askpass",
                "GIT_TERMINAL_PROMPT": "0",
                "EFP_GITHUB_TOKEN": token,
            },
            secrets=(token,),
        ),
    )

    result = await run_command("git", ["fetch"])
    assert captured_env["GIT_ASKPASS"] == "/tmp/askpass"
    assert captured_env["GIT_TERMINAL_PROMPT"] == "0"
    assert captured_env["EFP_GITHUB_TOKEN"] == token
    assert token not in str(result["meta"])


@pytest.mark.asyncio
async def test_run_command_non_git_non_gh_does_not_inject_tokens(monkeypatch):
    captured_env = {}

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await run_command("ls", ["-la"])
    assert result["ok"] is True
    assert "GH_TOKEN" not in captured_env
    assert "GITHUB_TOKEN" not in captured_env
    assert "GIT_ASKPASS" not in captured_env
    assert "EFP_GITHUB_TOKEN" not in captured_env


@pytest.mark.asyncio
async def test_run_command_cleanup_always_called(monkeypatch):
    cleanup_called = {"value": False}

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(
        "src.bash_tools.api._build_credential_env",
        lambda cmd, args=None, cwd=None: ToolCredentialEnv(
            env={"GH_TOKEN": "x"},
            cleanup=lambda: cleanup_called.__setitem__("value", True),
            secrets=("x",),
        ),
    )

    await run_command("gh", ["repo", "view"])
    assert cleanup_called["value"] is True
