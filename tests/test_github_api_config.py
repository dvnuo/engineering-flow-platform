import pytest

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
