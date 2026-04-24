from pathlib import Path


def test_github_tool_surface_has_no_legacy_base64_utf8_decode_path():
    github_init = Path("src/github/__init__.py").read_text(encoding="utf-8")
    github_api = Path("src/github/api.py").read_text(encoding="utf-8")

    assert "base64.b64decode(content).decode(\"utf-8\")" not in github_init
    assert "base64.b64decode(content).decode(\"utf-8\")" not in github_api
    assert "render_github_file_manifest" in github_init
    assert "render_github_file_manifest" in github_api


def test_tool_dispatch_passes_session_scope_to_github_get_file_content():
    dispatch_source = Path("src/__init__.py").read_text(encoding="utf-8")

    assert "session_id = kwargs.get(\"_session_id\")" in dispatch_source
    assert "_session_id=session_id" in dispatch_source
    assert "preview=preview" in dispatch_source
