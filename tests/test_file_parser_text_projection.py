import pytest

from tests._lightweight_file_parser_loader import load_file_parser_lightweight


@pytest.mark.asyncio
async def test_text_plain_parse_supported():
    module, cleanup = load_file_parser_lightweight()
    try:
        meta = await module.save_uploaded_file(b"hello\n\nworld", "a.txt", session_id="s1", content_type="text/plain")
        result = await module.parse_file(meta.file_id)
        assert result.success is True
        assert "hello" in result.markdown
        assert result.blocks
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_application_json_parse_supported():
    module, cleanup = load_file_parser_lightweight()
    try:
        meta = await module.save_uploaded_file(b'{\"a\":1}', "a.json", session_id="s1", content_type="application/json")
        result = await module.parse_file(meta.file_id)
        assert result.success is True
        assert '"a":1' in result.markdown
    finally:
        cleanup()
