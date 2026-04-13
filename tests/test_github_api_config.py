import pytest

from src.github.url_utils import normalize_github_api_base_url


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
    assert normalize_github_api_base_url(raw) == expected
