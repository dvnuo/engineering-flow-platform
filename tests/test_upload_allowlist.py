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


def test_get_safe_extension_accepts_known_formats_and_the_configured_allowlist(monkeypatch):
    monkeypatch.delenv(validators.UPLOAD_EXTENSIONS_ENV, raising=False)
    # Known formats stay recognisable even when off the allowlist (images are
    # off the non-visual default); unknown ones need the allowlist.
    assert validators.get_safe_extension("a.jpg") == ".jpg"
    assert validators.get_safe_extension("a.md") == ".md"
    assert validators.get_safe_extension("a.foo") == ""
    monkeypatch.setenv(validators.UPLOAD_EXTENSIONS_ENV, "foo")
    assert validators.get_safe_extension("a.foo") == ".foo"
    assert validators.get_safe_extension("a.exe") == ""


def test_default_allowlist_is_non_visual_and_includes_office_and_archives():
    defaults = validators.DEFAULT_UPLOAD_EXTENSIONS
    assert defaults == ("pdf", "docx", "xlsx", "csv", "txt", "log", "pptx", "zip", "md", "yaml", "yml", "json", "xml")
    assert not (set(defaults) & validators.IMAGE_EXTENSIONS)
    assert "pptx" in validators.BINARY_EXTENSION_MIME_TYPES
    assert "zip" in validators.BINARY_EXTENSION_MIME_TYPES


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
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", True),
        ("application/zip", True),
        ("image/bmp", False),
        ("application/octet-stream", False),
        ("application/x-dosexec", False),
        ("application/x-7z-compressed", False),
        ("", False),
    ],
)
def test_is_supported_upload_mime(mime, expected):
    assert validators.is_supported_upload_mime(mime) is expected


def _zip_bytes(entries):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_detect_mime_type_tells_office_documents_and_plain_archives_apart(monkeypatch):
    monkeypatch.delenv(validators.UPLOAD_EXTENSIONS_ENV, raising=False)
    pptx = _zip_bytes({"[Content_Types].xml": "<Types/>", "ppt/presentation.xml": "<p/>"})
    docx = _zip_bytes({"[Content_Types].xml": "<Types/>", "word/document.xml": "<w/>"})
    xlsx = _zip_bytes({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<x/>"})
    plain = _zip_bytes({"README.md": "# hi", "src/main.py": "print(1)"})

    assert _detect_mime_type(pptx, "deck.pptx") == validators.BINARY_EXTENSION_MIME_TYPES["pptx"]
    assert _detect_mime_type(docx, "doc.docx") == validators.BINARY_EXTENSION_MIME_TYPES["docx"]
    assert _detect_mime_type(xlsx, "book.xlsx") == validators.BINARY_EXTENSION_MIME_TYPES["xlsx"]
    assert _detect_mime_type(plain, "bundle.zip") == "application/zip"
    # The bytes decide: a plain archive named .docx is still an archive.
    assert _detect_mime_type(plain, "renamed.docx") == "application/zip"
    # A zip extension on non-zip bytes is not an archive.
    assert _detect_mime_type(b"MZ\x90\x00\xff\xfe", "fake.zip") == "application/octet-stream"


def test_detect_mime_type_maps_allowed_text_extensions(monkeypatch):
    monkeypatch.setenv(validators.UPLOAD_EXTENSIONS_ENV, "txt,md,json,yaml,log,py,csv")
    assert _detect_mime_type(b"# hi\n", "README.md") == "text/markdown"
    assert _detect_mime_type(b'{"a": 1}', "c.json") == "application/json"
    assert _detect_mime_type(b"a: 1\n", "c.yaml") == "application/yaml"
    assert _detect_mime_type(b"line\n", "app.log") == "text/plain"
    assert _detect_mime_type(b"print(1)\n", "main.py") == "text/plain"
    assert _detect_mime_type(b"a,b\n1,2\n", "d.csv") == "text/csv"
    assert _detect_mime_type(b"plain", "d.txt") == "text/plain"


def test_text_helpers_accept_utf8_and_gb18030_and_reject_binary():
    gbk = "第三季度营收增长 12%\n".encode("gb18030")
    assert validators.looks_like_text("plain ascii".encode()) is True
    assert validators.looks_like_text("中文 UTF-8".encode("utf-8")) is True
    assert validators.looks_like_text(gbk) is True
    assert validators.looks_like_text(b"MZ\x90\x00\x03") is False
    assert validators.looks_like_text(b"") is False

    assert validators.decode_text_bytes("中文".encode("utf-8")) == ("中文", "utf-8")
    assert validators.decode_text_bytes(b"\xef\xbb\xbfbom") == ("bom", "utf-8")
    assert validators.decode_text_bytes(gbk) == ("第三季度营收增长 12%\n", "gb18030")


def test_sanitize_filename_keeps_names_in_any_script():
    assert validators.sanitize_filename("2026-09 日志 (final).log") == "2026-09 日志 (final).log"
    assert validators.sanitize_filename("C:\\Users\\me\\报告.docx") == "报告.docx"
    assert validators.sanitize_filename("dir/../notes.txt") == "notes.txt"
    assert validators.sanitize_filename("bad\x00\x1fname.txt") == "badname.txt"
    assert validators.sanitize_filename("..hidden") == "hidden"
    assert validators.sanitize_filename("   ").startswith("file_")
    assert validators.sanitize_filename("") .startswith("file_")
    long_name = "a" * 300 + ".log"
    assert validators.sanitize_filename(long_name) == "a" * 196 + ".log"


def test_detect_mime_type_accepts_gbk_encoded_logs(monkeypatch):
    monkeypatch.setenv(validators.UPLOAD_EXTENSIONS_ENV, "log,txt")
    gbk_log = "2026-09-16 错误：连接超时\n".encode("gb18030")
    assert _detect_mime_type(gbk_log, "app.log") == "text/plain"


def test_detect_mime_type_rejects_binary_bytes_and_unlisted_extensions(monkeypatch):
    monkeypatch.setenv(validators.UPLOAD_EXTENSIONS_ENV, "txt,md")
    assert _detect_mime_type(b"MZ\x90\x00\xff\xfe\x00\x01", "notes.txt") == "application/octet-stream"
    # Not on the allowlist: never treated as text, even if it decodes.
    assert _detect_mime_type(b"print(1)\n", "main.py") == "application/octet-stream"
    # Known binary formats are still decided by signature, not by name.
    assert _detect_mime_type(b"%PDF-1.7\n", "doc.txt") == "application/pdf"
    assert _detect_mime_type(b"\x89PNG\r\n\x1a\n....", "shot.png") == "image/png"
