import pytest

from src.utils import attachment as attachment_mod


@pytest.mark.asyncio
async def test_attachment_pdf_projects_to_text(monkeypatch):
    async def _fake_download(url, auth_header=None):
        return (b"%PDF-1.4 fake", "application/pdf", "a.pdf")

    async def _fake_parse(file_id, options=None):
        class R:
            success = True
            markdown = "parsed pdf"
            blocks = []
            content_type = "application/pdf"
            filename = "a.pdf"
        return R()

    monkeypatch.setattr(attachment_mod, "_download_file", _fake_download)
    monkeypatch.setattr(attachment_mod, "parse_file", _fake_parse)

    out = await attachment_mod.download_and_process_attachment("u", source_type="jira", source_kind="issue_attachment")
    assert out.content_format == "text"
    assert out.content == "parsed pdf"
    assert out.artifact_id
    assert out.projection_kind


@pytest.mark.asyncio
async def test_attachment_binary_stays_metadata_only(monkeypatch):
    async def _fake_download(url, auth_header=None):
        return (b"\x00\x01\x02", "application/octet-stream", "a.bin")

    monkeypatch.setattr(attachment_mod, "_download_file", _fake_download)
    out = await attachment_mod.download_and_process_attachment("u")
    assert out.content.startswith("[application/octet-stream")
