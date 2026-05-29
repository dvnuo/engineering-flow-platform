from pathlib import Path


def test_github_tool_surface_has_no_legacy_base64_utf8_decode_path():
    github_init = Path("src/github/__init__.py").read_text(encoding="utf-8")
    github_api = Path("src/github/api.py").read_text(encoding="utf-8")

    assert "base64.b64decode(content).decode(\"utf-8\")" not in github_init
    assert "base64.b64decode(content).decode(\"utf-8\")" not in github_api
    assert "render_github_file_manifest" in github_init
    assert "render_github_file_manifest" in github_api


def test_legacy_github_file_tool_is_not_exposed_from_runtime_v2_root_dispatch():
    dispatch_source = Path("src/__init__.py").read_text(encoding="utf-8")

    assert "github_get_file_content" not in dispatch_source
