import importlib
from pathlib import Path

import pytest

from src.gateway import webchat
from src.github.url_utils import normalize_github_api_base_url


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (Path(tmp_path) / ".efp").mkdir(parents=True, exist_ok=True)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", "https://api.github.com"),
        ("https://api.github.com/", "https://api.github.com"),
        ("https://github.com", "https://api.github.com"),
        ("github.company.com", "https://github.company.com/api/v3"),
        ("https://github.company.com", "https://github.company.com/api/v3"),
        ("https://github.company.com/api/v3/", "https://github.company.com/api/v3"),
    ],
)
def test_normalize_github_api_base_url(raw, expected):
    assert normalize_github_api_base_url(raw) == expected


@pytest.mark.asyncio
async def test_github_channel_request_uses_normalized_base_url(monkeypatch, tmp_path):
    config_path = Path(tmp_path) / ".efp" / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")

    config_module = importlib.import_module("src.config")
    config_module = importlib.reload(config_module)
    monkeypatch.setattr(config_module.config, "config_path", config_path, raising=False)
    monkeypatch.setattr(
        config_module.config,
        "_config",
        {"github": {"base_url": "https://github.com", "enabled": True, "api_token": ""}},
        raising=False,
    )

    github_api = importlib.import_module("src.github.api")
    github_api = importlib.reload(github_api)

    channel = github_api.GitHubChannel()
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {}
        text = "{}"

        def json(self):
            return {"ok": True}

    async def fake_request(method, url, headers=None, **kwargs):
        captured["method"] = method
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(channel.client, "request", fake_request)

    result = await channel._request("GET", "/repos/acme/repo")

    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.github.com/repos/acme/repo"
    assert result == {"ok": True}


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("https://github.com", "https://api.github.com"),
        ("https://github.company.com", "https://github.company.com/api/v3"),
    ],
)
def test_webchat_helper_uses_github_base_url_normalization(monkeypatch, base_url, expected):
    monkeypatch.setattr(webchat.global_config, "_config", {"github": {"base_url": base_url}}, raising=False)
    assert webchat._get_github_api_base_url() == expected


def test_github_channel_reinit_uses_dot_notation_config(monkeypatch):
    github_api = importlib.import_module("src.github.api")
    github_api = importlib.reload(github_api)

    monkeypatch.setattr(
        github_api.config,
        "_config",
        {"github": {"base_url": "https://github.company.com", "enabled": True, "api_token": "abc"}},
        raising=False,
    )

    channel = github_api.GitHubChannel()
    channel.reinit()

    assert channel.base_url == "https://github.company.com/api/v3"
    assert channel.token == "abc"
    assert channel.enabled is True
    assert channel._headers.get("Authorization") == "Bearer abc"


def test_channels_github_reuses_canonical_singleton():
    github_module = importlib.import_module("src.github")
    github_module = importlib.reload(github_module)
    channels_github = importlib.import_module("src.channels.github")
    channels_github = importlib.reload(channels_github)

    assert github_module.github_channel is channels_github.github_channel
