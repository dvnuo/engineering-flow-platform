from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests._lightweight_attachment_loader import load_attachment_lightweight


@pytest.mark.asyncio
async def test_download_and_process_attachment_prefers_text_for_images_when_ocr_succeeds(monkeypatch):
    attachment, cleanup = load_attachment_lightweight()
    try:
        monkeypatch.setattr(
            attachment,
            "_download_file",
            AsyncMock(return_value=(b"img-bytes", "image/png", "diagram.png")),
        )

        metadata = SimpleNamespace(file_id="file-1", size=10, uploaded_at="2026-01-01")
        monkeypatch.setattr(attachment, "save_uploaded_file", AsyncMock(return_value=metadata))
        monkeypatch.setattr(attachment, "get_file_path", lambda _file_id: "/tmp/diagram.png")

        parse_mock = AsyncMock(return_value=SimpleNamespace(success=True, markdown="hello from image"))
        monkeypatch.setattr(attachment, "parse_file", parse_mock)

        compress_called = False

        def _compress(*args, **kwargs):
            nonlocal compress_called
            compress_called = True
            return "should-not-be-used"

        monkeypatch.setattr(attachment, "compress_image_for_llm", _compress)

        result = await attachment.download_and_process_attachment(
            url="https://example.com/diagram.png",
            options={"prefer_text_for_images": True, "vision_enabled": False},
        )

        assert result.content_format == "text"
        assert "hello from image" in result.content
        assert compress_called is False
        parse_mock.assert_awaited_once()
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_download_and_process_attachment_falls_back_to_base64_when_ocr_empty(monkeypatch):
    attachment, cleanup = load_attachment_lightweight()
    try:
        monkeypatch.setattr(
            attachment,
            "_download_file",
            AsyncMock(return_value=(b"img-bytes", "image/png", "diagram.png")),
        )

        metadata = SimpleNamespace(file_id="file-2", size=10, uploaded_at="2026-01-01")
        monkeypatch.setattr(attachment, "save_uploaded_file", AsyncMock(return_value=metadata))
        monkeypatch.setattr(attachment, "get_file_path", lambda _file_id: "/tmp/diagram.png")

        monkeypatch.setattr(
            attachment,
            "parse_file",
            AsyncMock(return_value=SimpleNamespace(success=False, markdown="")),
        )
        monkeypatch.setattr(attachment, "compress_image_for_llm", lambda *_args, **_kwargs: "abc123")

        result = await attachment.download_and_process_attachment(
            url="https://example.com/diagram.png",
            options={"prefer_text_for_images": True, "include_image_data": True},
        )

        assert result.content_format == "metadata"
        assert result.content == "[image/png: diagram.png]"
    finally:
        cleanup()
