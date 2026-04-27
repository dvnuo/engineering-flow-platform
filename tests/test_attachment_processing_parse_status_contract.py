import pytest

from tests._lightweight_attachment_loader import load_attachment_lightweight


@pytest.mark.asyncio
async def test_attachment_processing_parse_status_contract_success_failed_skipped(monkeypatch):
    attachment_mod, cleanup = load_attachment_lightweight()

    async def _fake_download_text(url, auth_header=None):
        return (b"hello", "text/plain", "a.txt")

    async def _fake_parse_success(file_id, options=None):
        class R:
            success = True
            markdown = "parsed text"
            blocks = []
            content_type = "text/plain"
            filename = "a.txt"

        return R()

    async def _fake_parse_failed(file_id, options=None):
        class R:
            success = False
            error = "parse failed"

        return R()

    monkeypatch.setattr(attachment_mod, "_download_file", _fake_download_text)

    try:
        monkeypatch.setattr(attachment_mod, "parse_file", _fake_parse_success)
        ok = await attachment_mod.download_and_process_attachment("u")
        assert ok.parse_status == "completed"
        assert ok.projected_to_text is True

        monkeypatch.setattr(attachment_mod, "parse_file", _fake_parse_failed)
        failed = await attachment_mod.download_and_process_attachment("u")
        assert failed.parse_status == "failed"
        assert failed.projected_to_text is False

        async def _fake_download_bin(url, auth_header=None):
            return (b"\x00\x01", "application/octet-stream", "a.bin")

        monkeypatch.setattr(attachment_mod, "_download_file", _fake_download_bin)
        skipped = await attachment_mod.download_and_process_attachment("u")
        assert skipped.parse_status == "skipped"
        assert skipped.projected_to_text is False
    finally:
        cleanup()
