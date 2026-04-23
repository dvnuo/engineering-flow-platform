"""Parser for text-like files."""

from datetime import datetime
import re
from pathlib import Path
from typing import Optional

from .models import Block, ParseResult


def _decode_text(content: bytes) -> str:
    """Decode bytes with resilient UTF-8 fallbacks."""
    for encoding, errors in (("utf-8-sig", "strict"), ("utf-8", "strict"), ("utf-8", "replace")):
        try:
            return content.decode(encoding, errors=errors)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _split_long_paragraph(paragraph: str, target_size: int = 1400) -> list[str]:
    """Split long paragraph into deterministic chunks."""
    text = paragraph.strip()
    if len(text) <= target_size:
        return [text] if text else []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + target_size, len(text))
        if end < len(text):
            split_at = text.rfind(" ", start, end)
            if split_at > start + (target_size // 2):
                end = split_at
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end
    return chunks


async def parse_text_file(path: str, options: Optional[dict] = None) -> ParseResult:
    """Parse text-like file content into markdown and paragraph blocks."""
    import asyncio
    import time

    start = time.time()
    options = options or {}
    file_id = options.get("file_id", "unknown")
    filename = options.get("filename", path.split("/")[-1])
    content_type = options.get("content_type", "text/plain")

    raw = await asyncio.to_thread(Path(path).read_bytes)
    text = _decode_text(raw)
    markdown = text

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p and p.strip()]
    extracted_at = datetime.utcnow().isoformat() + "Z"
    blocks: list[Block] = []

    chunk_index = 1
    for paragraph in paragraphs:
        for piece in _split_long_paragraph(paragraph):
            blocks.append(
                Block(
                    chunk_id=f"{file_id}_text_1_{chunk_index}",
                    type="paragraph",
                    content=piece,
                    markdown=piece,
                    page=None,
                    sheet=None,
                    method="text",
                    confidence=1.0,
                    extracted_at=extracted_at,
                )
            )
            chunk_index += 1

    parse_time_ms = int((time.time() - start) * 1000)
    return ParseResult(
        success=True,
        content_type=content_type,
        file_id=file_id,
        filename=filename,
        markdown=markdown,
        blocks=blocks,
        json={"blocks": len(blocks), "chars": len(markdown)},
        parse_time_ms=parse_time_ms,
    )
