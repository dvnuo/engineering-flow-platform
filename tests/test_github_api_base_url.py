from pathlib import Path

import pytest

from tests._lightweight_runtime_loaders import load_github_url_utils_lightweight


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
    module = load_github_url_utils_lightweight()
    assert module.normalize_github_api_base_url(raw) == expected


def test_webchat_helper_wiring_uses_normalizer_source_contract():
    source = Path("src/gateway/webchat.py").read_text(encoding="utf-8")
    assert "from src.github.url_utils import normalize_github_api_base_url" in source
    assert "def _get_github_api_base_url() -> str:" in source
    assert "return normalize_github_api_base_url(global_config.get(\"github.base_url\"))" in source
