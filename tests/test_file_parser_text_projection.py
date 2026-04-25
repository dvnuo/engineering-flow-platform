import pytest


@pytest.mark.asyncio
async def test_text_plain_parse_supported():
    try:
        from src.utils.file_parser import save_uploaded_file, parse_file
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"file_parser import unavailable in this environment: {exc}")
    meta = await save_uploaded_file(b"hello\n\nworld", "a.txt", session_id="s1", content_type="text/plain")
    result = await parse_file(meta.file_id)
    assert result.success is True
    assert "hello" in result.markdown
    assert result.blocks


@pytest.mark.asyncio
async def test_application_json_parse_supported():
    try:
        from src.utils.file_parser import save_uploaded_file, parse_file
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"file_parser import unavailable in this environment: {exc}")
    meta = await save_uploaded_file(b'{"a":1}', "a.json", session_id="s1", content_type="application/json")
    result = await parse_file(meta.file_id)
    assert result.success is True
    assert '"a":1' in result.markdown
