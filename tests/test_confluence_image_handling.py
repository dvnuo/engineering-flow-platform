import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_confluence_get_page_by_url_processes_attachments_with_instance_channel():
    with patch("src.confluence.confluence_channel") as mock_channel, patch(
        "src.confluence.ConfluenceFormatAdapter.get_page", new_callable=AsyncMock
    ) as mock_get_page, patch(
        "src.confluence.download_and_process_attachment", new_callable=AsyncMock
    ) as mock_download:
        mock_channel.is_configured.return_value = True

        instance_channel = MagicMock()
        instance_channel.is_configured.return_value = True
        instance_channel.base_url = "https://right.example/wiki"
        instance_channel._auth_header = {"Authorization": "Basic abc"}
        instance_channel.get_attachments = AsyncMock(
            return_value=[
                {
                    "title": "diagram.png",
                    "extensions": {"fileSize": 12345},
                    "_links": {"download": "/download/attachments/123/diagram.png"},
                }
            ]
        )
        mock_channel.get_instance_client.return_value = instance_channel

        mock_get_page.return_value = "# Test Page\n\n![diagram.png](attachment:diagram.png)"
        mock_download.return_value = SimpleNamespace(
            content_type="image/png",
            content_format="text",
            content="timeout 30s",
        )

        from src.confluence import confluence_get_page_by_url

        result = await confluence_get_page_by_url(
            "https://right.example/wiki/spaces/SPACE/pages/123456/Page-Title",
            preview=True,
        )

        assert "# Test Page" in result
        assert "**Attachments:**" in result
        assert "image attachment not auto-expanded" in result

        mock_channel.get_instance_client.assert_called_once_with(
            url="https://right.example/wiki/spaces/SPACE/pages/123456/Page-Title",
            strict=True,
        )
        mock_download.assert_not_called()


@pytest.mark.asyncio
async def test_confluence_get_page_does_not_emit_raw_base64_for_image_attachment():
    with patch("src.confluence.confluence_channel") as mock_channel, patch(
        "src.confluence.ConfluenceFormatAdapter.get_page", new_callable=AsyncMock
    ) as mock_get_page, patch(
        "src.confluence.download_and_process_attachment", new_callable=AsyncMock
    ) as mock_download:
        mock_channel.is_configured.return_value = True
        mock_channel.base_url = "https://company.atlassian.net/wiki"
        mock_channel._auth_header = {"Authorization": "Basic xyz"}
        mock_channel.get_attachments = AsyncMock(
            return_value=[
                {
                    "title": "image.png",
                    "extensions": {"fileSize": 500},
                    "_links": {"download": "/download/attachments/1/image.png"},
                }
            ]
        )

        mock_get_page.return_value = "# Page"
        mock_download.return_value = SimpleNamespace(
            content_type="image/png",
            content_format="base64",
            content="data:image/png;base64,VERY_LONG_BLOB",
        )

        from src.confluence import confluence_get_page

        result = await confluence_get_page("1", preview=True)

        assert "**Attachments:**" in result
        assert "image.png" in result
        assert "VERY_LONG_BLOB" not in result
        assert "image attachment not auto-expanded" in result
        mock_download.assert_not_called()


@pytest.mark.asyncio
async def test_confluence_get_page_by_url_works_even_if_default_channel_not_configured_when_matched_instance_is_configured():
    with patch("src.confluence.confluence_channel") as mock_channel, patch(
        "src.confluence.ConfluenceFormatAdapter.get_page", new_callable=AsyncMock
    ) as mock_get_page:
        mock_channel.is_configured.return_value = False

        instance_channel = MagicMock()
        instance_channel.is_configured.return_value = True
        instance_channel.base_url = "https://right.example/wiki"
        instance_channel._auth_header = {"Authorization": "Basic abc"}
        instance_channel.get_attachments = AsyncMock(return_value=[])
        mock_channel.get_instance_client.return_value = instance_channel

        mock_get_page.return_value = "# Test Page"

        from src.confluence import confluence_get_page_by_url

        url = "https://right.example/wiki/spaces/SPACE/pages/123456/Page-Title"
        result = await confluence_get_page_by_url(url, preview=True)

        assert "# Test Page" in result
        mock_channel.get_instance_client.assert_called_once_with(url=url, strict=True)


@pytest.mark.asyncio
async def test_confluence_get_page_by_url_returns_instance_error_when_url_does_not_match_any_configured_instance():
    with patch("src.confluence.confluence_channel") as mock_channel, patch(
        "src.confluence.ConfluenceFormatAdapter.get_page", new_callable=AsyncMock
    ) as mock_get_page, patch(
        "src.confluence.download_and_process_attachment", new_callable=AsyncMock
    ) as mock_download:
        mock_channel.get_instance_client.return_value = None

        from src.confluence import confluence_get_page_by_url

        result = await confluence_get_page_by_url(
            "https://unknown.example/wiki/spaces/SPACE/pages/123456/Page-Title",
            preview=True,
        )

        assert "Confluence instance for URL is not configured" in result
        mock_get_page.assert_not_called()
        mock_download.assert_not_called()
