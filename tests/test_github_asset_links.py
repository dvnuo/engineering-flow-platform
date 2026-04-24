import importlib.util
from pathlib import Path


def _load_asset_links_module():
    module_path = Path("src/github/asset_links.py")
    spec = importlib.util.spec_from_file_location("github_asset_links_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_extract_markdown_and_bare_urls_in_order_with_dedup():
    m = _load_asset_links_module()
    text = (
        "![img](https://github.com/user-attachments/assets/a1) "
        "[doc](https://example.com/a) "
        "see https://github.com/user-attachments/assets/a1 and "
        "https://raw.githubusercontent.com/user-attachments/assets/a2"
    )
    links = m.extract_markdown_links(text)
    assert links == [
        "https://github.com/user-attachments/assets/a1",
        "https://example.com/a",
        "https://raw.githubusercontent.com/user-attachments/assets/a2",
    ]


def test_extract_github_asset_urls_filters_external_links():
    m = _load_asset_links_module()
    text = (
        "https://example.com/nope "
        "https://github.com/user-attachments/assets/a1 "
        "https://raw.githubusercontent.com/user-attachments/assets/a2 "
        "https://my.ghe.local/assets/x"
    )
    urls = m.extract_github_asset_urls(text, github_base_host="my.ghe.local")
    assert "https://example.com/nope" not in urls
    assert "https://github.com/user-attachments/assets/a1" in urls
    assert "https://raw.githubusercontent.com/user-attachments/assets/a2" in urls
    assert "https://my.ghe.local/assets/x" in urls
