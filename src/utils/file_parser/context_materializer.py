"""Helpers for parsing files and materializing into file-context storage."""

from typing import Optional

from src.hooks.file_context.models import Chunk, SessionFileMeta
from src.hooks.file_context.storage import storage as context_storage
from src.utils.file_parser.models import ParseResult
from src.utils.file_parser.storage import get_metadata


def ensure_session_file_registered(session_id: str, file_id: str) -> SessionFileMeta:
    """Ensure SessionFileMeta exists for file in session context storage."""
    existing = context_storage.get_file_meta(session_id, file_id)
    if existing:
        return existing

    metadata = get_metadata(file_id)
    session_meta = SessionFileMeta(
        file_id=file_id,
        session_id=session_id,
        filename=metadata.original_filename,
        content_type=metadata.content_type,
        parse_status="pending",
    )
    context_storage.add_file_to_session(session_id, session_meta)
    return session_meta


def materialize_parse_result(session_id: str, file_id: str, result: ParseResult) -> dict:
    """Persist parse result blocks into file-context chunks and update status."""
    ensure_session_file_registered(session_id, file_id)
    if not result.success:
        context_storage.update_file_status(
            session_id=session_id,
            file_id=file_id,
            status="failed",
            error=result.error,
        )
        return {"status": "failed", "chunk_count": 0, "total_chars": 0}

    context_storage.delete_file_chunks(file_id)
    chunks = []
    total_chars = 0
    for index, block in enumerate(result.blocks or [], start=1):
        content = block.content or ""
        total_chars += len(content)
        chunks.append(
            Chunk(
                chunk_id=block.chunk_id,
                file_id=file_id,
                session_id=session_id,
                type=block.type,
                content=content,
                markdown=block.markdown,
                page=block.page,
                index=index,
                row_range=block.row_range,
                source=block.method,
                confidence=block.confidence,
                content_hash=context_storage.compute_content_hash(content),
                bbox=block.bbox,
            )
        )

    if chunks:
        context_storage.save_chunks(chunks)
    context_storage.update_file_status(
        session_id=session_id,
        file_id=file_id,
        status="completed",
        chunk_count=len(chunks),
        total_chars=total_chars,
    )
    return {"status": "completed", "chunk_count": len(chunks), "total_chars": total_chars}


async def ensure_file_parsed_for_session(
    file_id: str,
    session_id: str,
    options: Optional[dict] = None,
) -> ParseResult:
    """Ensure file is parsed+materialized for a session and return parse result."""
    from src.utils.file_parser import parse_file

    ensure_session_file_registered(session_id, file_id)
    context_storage.update_file_status(session_id=session_id, file_id=file_id, status="processing")

    result = await parse_file(file_id, options or {})
    materialize_parse_result(session_id, file_id, result)
    return result
