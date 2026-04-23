from types import SimpleNamespace

import pytest

from src.utils.file_parser.models import Block, ParseResult
from src.utils.file_parser.text import parse_text_file
from src.utils.file_parser.context_materializer import materialize_parse_result


@pytest.mark.asyncio
async def test_parse_text_file_supports_plain_text(tmp_path):
    sample = tmp_path / "notes.txt"
    sample.write_text("Line one.\n\nLine two.", encoding="utf-8")

    result = await parse_text_file(
        str(sample),
        {"file_id": "f-1", "filename": "notes.txt", "content_type": "text/plain"},
    )

    assert result.success is True
    assert result.content_type == "text/plain"
    assert "Line one." in result.markdown
    assert len(result.blocks) >= 2
    assert result.blocks[0].method == "text"


@pytest.mark.asyncio
async def test_parse_text_file_supports_json_mime(tmp_path):
    sample = tmp_path / "data.json"
    sample.write_text('{"a": 1, "b": 2}', encoding="utf-8")

    result = await parse_text_file(
        str(sample),
        {"file_id": "f-2", "filename": "data.json", "content_type": "application/json"},
    )

    assert result.success is True
    assert result.content_type == "application/json"
    assert result.blocks


def test_materialize_parse_result_from_pydantic_blocks(monkeypatch):
    from src.utils.file_parser import context_materializer as cm

    saved_chunks = []
    statuses = []
    monkeypatch.setattr(cm, "ensure_session_file_registered", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(cm.context_storage, "delete_file_chunks", lambda _file_id: 0)
    monkeypatch.setattr(cm.context_storage, "save_chunks", lambda chunks: saved_chunks.extend(chunks))
    monkeypatch.setattr(
        cm.context_storage,
        "update_file_status",
        lambda **kwargs: statuses.append(kwargs),
    )

    result = ParseResult(
        success=True,
        content_type="text/plain",
        file_id="file-1",
        filename="notes.txt",
        blocks=[
            Block(
                chunk_id="file-1_text_1_1",
                type="paragraph",
                content="hello world",
                method="text",
                confidence=1.0,
                extracted_at="2026-01-01T00:00:00Z",
            )
        ],
    )

    summary = materialize_parse_result("session-1", "file-1", result)
    assert summary["status"] == "completed"
    assert summary["chunk_count"] == 1
    assert saved_chunks[0].chunk_id == "file-1_text_1_1"
    assert saved_chunks[0].source == "text"
    assert statuses[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_parse_file_returns_failure_for_unsupported_binary(monkeypatch, tmp_path):
    from src.utils import file_parser

    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\xff\x01\x02")
    monkeypatch.setattr(file_parser, "get_file_path", lambda _file_id: binary)
    monkeypatch.setattr(
        file_parser,
        "get_metadata",
        lambda _file_id: SimpleNamespace(content_type="application/octet-stream", original_filename="blob.bin"),
    )

    result = await file_parser.parse_file("file-bin")
    assert result.success is False
