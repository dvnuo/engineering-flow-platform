"""Chat upload allowlist (EFP_CHAT_UPLOAD_EXTENSIONS) and size cap helpers."""

from __future__ import annotations

import pytest

from src.utils.file_parser import validators
from src.utils.file_parser import _detect_mime_type


def test_parse_upload_extensions_normalizes_dots_case_whitespace_and_duplicates():
    assert validators.parse_upload_extensions(" .PDF, md ,,txt;yml\n.md ") == ["pdf", "md", "txt", "yml"]
    assert validators.parse_upload_extensions("jpg jpeg png") == ["jpg", "jpeg", "png"]


def test_parse_upload_extensions_falls_back_to_defaults_when_empty_or_garbage():
    defaults = list(validators.DEFAULT_UPLOAD_EXTENSIONS)
    assert validators.parse_upload_extensions("") == defaults
    assert validators.parse_upload_extensions(None) == defaults
    assert validators.parse_upload_extensions("*.*, ???") == defaults


def test_allowed_upload_extensions_reads_env(monkeypatch):
    monkeypatch.delenv(validators.UPLOAD_EXTENSIONS_ENV, raising=False)
    assert validators.allowed_upload_extensions() == list(validators.DEFAULT_UPLOAD_EXTENSIONS)
    monkeypatch.setenv(validators.UPLOAD_EXTENSIONS_ENV, "txt,md")
    assert validators.allowed_upload_extensions() == ["txt", "md"]


def test_is_upload_extension_allowed(monkeypatch):
    monkeypatch.setenv(validators.UPLOAD_EXTENSIONS_ENV, "txt,md")
    assert validators.is_upload_extension_allowed("notes.TXT") is True
    assert validators.is_upload_extension_allowed("dir/readme.md") is True
    assert validators.is_upload_extension_allowed("doc.pdf") is False
    assert validators.is_upload_extension_allowed("noext") is False
    assert validators.is_upload_extension_allowed("doc.pdf", allowed=["pdf"]) is True


def test_get_safe_extension_follows_the_configured_allowlist(monkeypatch):
    monkeypatch.delenv(validators.UPLOAD_EXTENSIONS_ENV, raising=False)
    assert validators.get_safe_extension("a.md") == ""
    monkeypatch.setenv(validators.UPLOAD_EXTENSIONS_ENV, "md")
    assert validators.get_safe_extension("a.md") == ".md"
    assert validators.get_safe_extension("a.jpg") == ""


def test_resolve_max_upload_mb(monkeypatch):
    monkeypatch.delenv(validators.MAX_UPLOAD_MB_ENV, raising=False)
    assert validators.resolve_max_upload_mb() == validators.DEFAULT_MAX_UPLOAD_MB == 25
    monkeypatch.setenv(validators.MAX_UPLOAD_MB_ENV, "40")
    assert validators.resolve_max_upload_mb() == 40
    monkeypatch.setenv(validators.MAX_UPLOAD_MB_ENV, "0")
    assert validators.resolve_max_upload_mb() == 25
    monkeypatch.setenv(validators.MAX_UPLOAD_MB_ENV, "lots")
    assert validators.resolve_max_upload_mb() == 25


@pytest.mark.parametrize(
    "mime, expected",
    [
        ("image/png", True),
        ("image/jpeg", True),
        ("application/pdf", True),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", True),
        ("text/plain", True),
        ("text/markdown; charset=utf-8", True),
        ("text/x-python", True),
        ("application/json", True),
        ("application/yaml", True),
        ("image/bmp", False),
        ("application/octet-stream", False),
        ("application/x-dosexec", False),
        ("application/zip", False),
        ("", False),
    ],
)
def test_is_supported_upload_mime(mime, expected):
    assert validators.is_supported_upload_mime(mime) is expected


def test_detect_mime_type_maps_allowed_text_extensions(monkeypatch):
    monkeypatch.setenv(validators.UPLOAD_EXTENSIONS_ENV, "txt,md,json,yaml,log,py,csv")
    assert _detect_mime_type(b"# hi\n", "README.md") == "text/markdown"
    assert _detect_mime_type(b'{"a": 1}', "c.json") == "application/json"
    assert _detect_mime_type(b"a: 1\n", "c.yaml") == "application/yaml"
    assert _detect_mime_type(b"line\n", "app.log") == "text/plain"
    assert _detect_mime_type(b"print(1)\n", "main.py") == "text/plain"
    assert _detect_mime_type(b"a,b\n1,2\n", "d.csv") == "text/csv"
    assert _detect_mime_type(b"plain", "d.txt") == "text/plain"


def test_detect_mime_type_rejects_binary_bytes_and_unlisted_extensions(monkeypatch):
    monkeypatch.setenv(validators.UPLOAD_EXTENSIONS_ENV, "txt,md")
    assert _detect_mime_type(b"MZ\x90\x00\xff\xfe\x00\x01", "notes.txt") == "application/octet-stream"
    # Not on the allowlist: never treated as text, even if it decodes.
    assert _detect_mime_type(b"print(1)\n", "main.py") == "application/octet-stream"
    # Known binary formats are still decided by signature, not by name.
    assert _detect_mime_type(b"%PDF-1.7\n", "doc.txt") == "application/pdf"
    assert _detect_mime_type(b"\x89PNG\r\n\x1a\n....", "shot.png") == "image/png"
