import pytest

from src.github.api import GitHubChannel
from tests._lightweight_runtime_loaders import load_github_url_utils_lightweight


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", "https://api.github.com"),
        ("https://api.github.com/", "https://api.github.com"),
        ("https://github.company.com", "https://github.company.com/api/v3"),
        ("github.company.com", "https://github.company.com/api/v3"),
        ("https://github.company.com/api/v3/", "https://github.company.com/api/v3"),
    ],
)
def test_normalize_github_api_base_url_config_semantics(raw, expected):
    module = load_github_url_utils_lightweight()
    assert module.normalize_github_api_base_url(raw) == expected


@pytest.mark.asyncio
async def test_github_channel_request_rejects_when_disabled():
    channel = GitHubChannel()
    try:
        channel.enabled = False
        channel.token = "token"

        with pytest.raises(RuntimeError, match="disabled"):
            await channel._request("GET", "/rate_limit")
    finally:
        await channel.client.aclose()


@pytest.mark.asyncio
async def test_github_channel_request_rejects_when_token_missing():
    channel = GitHubChannel()
    try:
        channel.enabled = True
        channel.token = ""

        with pytest.raises(RuntimeError, match="token"):
            await channel._request("GET", "/rate_limit")
    finally:
        await channel.client.aclose()
