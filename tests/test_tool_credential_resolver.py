import importlib.util
import sys
from pathlib import Path


def _load_resolver():
    spec = importlib.util.spec_from_file_location(
        "test_credential_resolver_module",
        Path("src/runtime/credential_resolver.py"),
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["test_credential_resolver_module"] = module
    spec.loader.exec_module(module)
    return module


def test_build_gh_env_public_github(monkeypatch):
    resolver = _load_resolver()
    token = "ghp_test_token"

    def fake_get(key, default=None):
        if key == "github":
            return {"enabled": True, "api_token": token, "base_url": ""}
        return default

    monkeypatch.setattr(resolver.config, "get", fake_get)
    env = resolver.build_env_for_command("gh", [], None)
    gh_config_dir = env.env.get("GH_CONFIG_DIR")

    assert env.env["GH_TOKEN"] == token
    assert env.env["GITHUB_TOKEN"] == token
    assert env.env["GH_PROMPT_DISABLED"] == "1"
    assert gh_config_dir and Path(gh_config_dir).exists()
    assert "GH_ENTERPRISE_TOKEN" not in env.env

    env.cleanup()
    assert not Path(gh_config_dir).exists()


def test_build_gh_env_enterprise(monkeypatch):
    resolver = _load_resolver()
    token = "ghe_test_token"

    def fake_get(key, default=None):
        if key == "github":
            return {
                "enabled": True,
                "api_token": token,
                "base_url": "https://github.company.com/api/v3",
            }
        return default

    monkeypatch.setattr(resolver.config, "get", fake_get)
    env = resolver.build_env_for_command("gh", [], None)
    gh_config_dir = env.env.get("GH_CONFIG_DIR")

    assert env.env["GH_HOST"] == "github.company.com"
    assert env.env["GH_ENTERPRISE_TOKEN"] == token
    assert env.env["GITHUB_ENTERPRISE_TOKEN"] == token
    assert "GH_TOKEN" not in env.env
    assert gh_config_dir and Path(gh_config_dir).exists()

    env.cleanup()
    assert not Path(gh_config_dir).exists()


def test_build_git_askpass_env(monkeypatch):
    resolver = _load_resolver()
    token = "ghp_test_token"

    def fake_get(key, default=None):
        if key == "github":
            return {"enabled": True, "api_token": token, "base_url": ""}
        return default

    monkeypatch.setattr(resolver.config, "get", fake_get)
    env = resolver.build_env_for_command("git", ["fetch"], None)
    askpass_path = env.env.get("GIT_ASKPASS")

    assert askpass_path and Path(askpass_path).exists()
    assert env.env["GIT_TERMINAL_PROMPT"] == "0"
    assert env.env["EFP_GITHUB_TOKEN"] == token

    env.cleanup()
    assert not Path(askpass_path).exists()


def test_disabled_or_empty_token_gets_no_env(monkeypatch):
    resolver = _load_resolver()
    def fake_get_disabled(key, default=None):
        if key == "github":
            return {"enabled": False, "api_token": "ghp_test_token", "base_url": ""}
        return default

    monkeypatch.setattr(resolver.config, "get", fake_get_disabled)
    assert resolver.build_env_for_command("gh", [], None).env == {}
    assert resolver.build_env_for_command("git", [], None).env == {}

    def fake_get_empty(key, default=None):
        if key == "github":
            return {"enabled": True, "api_token": "", "base_url": ""}
        return default

    monkeypatch.setattr(resolver.config, "get", fake_get_empty)
    assert resolver.build_env_for_command("gh", [], None).env == {}
    assert resolver.build_env_for_command("git", [], None).env == {}


def test_redact_text(monkeypatch):
    resolver = _load_resolver()
    token = "ghp_test_token"

    def fake_get(key, default=None):
        if key == "github":
            return {"enabled": True, "api_token": token, "base_url": ""}
        return default

    monkeypatch.setattr(resolver.config, "get", fake_get)
    env = resolver.build_env_for_command("gh", [], None)
    assert env.redact_text(f"hello {token}") == "hello [REDACTED_GITHUB_TOKEN]"
    env.cleanup()
