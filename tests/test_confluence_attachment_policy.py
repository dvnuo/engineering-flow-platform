from unittest.mock import AsyncMock

import pytest

from src.utils.attachment import AttachmentResult


@pytest.mark.asyncio
async def test_confluence_image_attachments_are_metadata_only_and_not_downloaded(monkeypatch):
    import src.confluence as confluence

    monkeypatch.setattr(
        confluence.confluence_channel,
        "get_attachments",
        AsyncMock(
            return_value=[
                {
                    "title": "step-01.png",
                    "extensions": {"fileSize": 187622},
                    "_links": {"download": "/download/attachments/123/step-01.png"},
                    "metadata": {"mediaType": "image/png"},
                }
            ]
        ),
    )

    mock_download = AsyncMock()
    monkeypatch.setattr(confluence, "download_and_process_attachment", mock_download)

    result = await confluence._process_confluence_attachments("123")

    assert mock_download.await_count == 0
    assert "step-01.png" in result
    assert "image attachment not auto-expanded" in result
    assert "Extracted text" not in result
    assert "data:image" not in result
    assert "base64" not in result


@pytest.mark.asyncio
async def test_confluence_text_attachment_still_uses_existing_preview_flow(monkeypatch):
    import src.confluence as confluence

    monkeypatch.setattr(
        confluence.confluence_channel,
        "get_attachments",
        AsyncMock(
            return_value=[
                {
                    "title": "notes.txt",
                    "extensions": {"fileSize": 120},
                    "_links": {"download": "/download/attachments/123/notes.txt"},
                    "metadata": {"mediaType": "text/plain"},
                }
            ]
        ),
    )

    mock_download = AsyncMock(
        return_value=AttachmentResult(
            file_id="file-1",
            content_type="text/plain",
            content="hello from attachment",
            content_format="text",
            filename="notes.txt",
            metadata={},
        )
    )
    monkeypatch.setattr(confluence, "download_and_process_attachment", mock_download)

    result = await confluence._process_confluence_attachments("123")

    assert mock_download.await_count == 1
    assert "notes.txt" in result
    assert "hello from attachment" in result


@pytest.mark.asyncio
async def test_confluence_attachment_output_is_bounded_and_reports_omitted_count(monkeypatch):
    import src.confluence as confluence

    attachments = [
        {
            "title": f"step-{idx}.png",
            "extensions": {"fileSize": 100 + idx},
            "_links": {"download": f"/download/attachments/123/step-{idx}.png"},
            "metadata": {"mediaType": "image/png"},
        }
        for idx in range(1, 7)
    ]

    monkeypatch.setattr(
        confluence.confluence_channel,
        "get_attachments",
        AsyncMock(return_value=attachments),
    )

    mock_download = AsyncMock()
    monkeypatch.setattr(confluence, "download_and_process_attachment", mock_download)

    result = await confluence._process_confluence_attachments("123")

    assert "showing first 5 of 6" in result
    assert "... and 1 more attachment(s) omitted" in result
    assert mock_download.await_count == 0


@pytest.mark.asyncio
async def test_unexpected_base64_result_is_not_inlined(monkeypatch):
    import src.confluence as confluence

    monkeypatch.setattr(
        confluence.confluence_channel,
        "get_attachments",
        AsyncMock(
            return_value=[
                {
                    "title": "artifact.bin",
                    "extensions": {"fileSize": 999},
                    "_links": {"download": "/download/attachments/123/artifact.bin"},
                    "metadata": {"mediaType": "application/octet-stream"},
                }
            ]
        ),
    )

    mock_download = AsyncMock(
        return_value=AttachmentResult(
            file_id="file-2",
            content_type="application/octet-stream",
            content="AAAABBBBCCCCDDDDEEEE",
            content_format="base64",
            filename="artifact.bin",
            metadata={},
        )
    )
    monkeypatch.setattr(confluence, "download_and_process_attachment", mock_download)

    result = await confluence._process_confluence_attachments("123")

    assert "AAAABBBBCCCCDDDDEEEE" not in result
    assert "[binary attachment omitted]" in result
