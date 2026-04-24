from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import Block, ParseResult


def _split_paragraphs(text: str, max_block_chars: int = 1200) -> list[str]:
    parts: list[str] = []
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if len(para) <= max_block_chars:
            parts.append(para)
            continue
        lines = [line for line in para.split("\n") if line.strip()]
        if len(lines) > 1:
            current = ""
            for line in lines:
                candidate = f"{current}\n{line}".strip()
                if current and len(candidate) > max_block_chars:
                    parts.append(current)
                    current = line
                else:
                    current = candidate
            if current:
                parts.append(current)
        else:
            for i in range(0, len(para), max_block_chars):
                parts.append(para[i : i + max_block_chars])
    return parts


async def parse_text_file(path: str, *, file_id: str, filename: str, content_type: str) -> ParseResult:
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    for idx, para in enumerate(_split_paragraphs(text), 1):
        blocks.append(
            Block(
                chunk_id=f"{file_id}_text_1_{idx}",
                type="paragraph",
                content=para,
                markdown=para,
                method="text",
                confidence=1.0,
                extracted_at=datetime.utcnow().isoformat() + "Z",
            )
        )

    return ParseResult(
        success=True,
        content_type=content_type,
        file_id=file_id,
        filename=filename,
        markdown=text,
        blocks=blocks,
    )
