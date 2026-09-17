"""ZIP archive parser: the file listing plus the text of the files inside.

A chat attachment that is an archive is projected the same way any other
attachment is: as markdown the model can read. The listing is always
included; text files inside are inlined up to fixed budgets so a large or
hostile archive (many entries, huge members, extreme compression ratios)
cannot blow up memory or the model context. Binary members and nested
archives are listed but never opened.
"""

from __future__ import annotations

import time
import zipfile
from datetime import datetime
from pathlib import PurePosixPath
from typing import Dict, List, Optional

from .models import Block, ParseResult
from .text import _split_paragraphs
from .validators import TEXT_EXTENSION_MIME_TYPES, decode_text_bytes, looks_like_text

ZIP_MIME_TYPE = "application/zip"

# Entries whose extension marks them as text; anything else is inlined only
# when its bytes look like UTF-8 text (see _looks_like_text).
TEXT_ENTRY_EXTENSIONS = set(TEXT_EXTENSION_MIME_TYPES) | {
    "py", "pyi", "js", "mjs", "cjs", "ts", "tsx", "jsx", "java", "kt", "kts", "go", "rs",
    "c", "h", "cc", "cpp", "hpp", "cs", "rb", "php", "swift", "scala", "sql", "sh", "bash",
    "zsh", "ps1", "bat", "cmd", "groovy", "gradle", "dart", "lua", "r", "pl", "pm",
    "css", "scss", "less", "vue", "svelte", "env", "toml", "ini", "cfg", "conf",
    "properties", "rst", "adoc", "tex", "srt", "vtt", "mmd", "mermaid", "puml",
    "plantuml", "jsonl", "ndjson", "ipynb", "gitignore", "dockerignore", "editorconfig",
}
ARCHIVE_ENTRY_EXTENSIONS = {"zip", "jar", "war", "ear", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "zst"}
NOISE_PREFIXES = ("__MACOSX/",)
NOISE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

MAX_LISTED_ENTRIES = 2000
MAX_TEXT_FILES = 200
MAX_FILE_CHARS = 20_000
MAX_TOTAL_CHARS = 200_000
MAX_ENTRY_BYTES = 5 * 1024 * 1024
MAX_TOTAL_READ_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _entry_extension(name: str) -> str:
    base = PurePosixPath(name).name
    if base.startswith(".") and base.count(".") == 1:
        # ".gitignore", ".env": the whole name is the "extension".
        return base[1:].lower()
    return PurePosixPath(base).suffix.lower().lstrip(".")


def _is_noise(name: str) -> bool:
    if any(name.startswith(prefix) for prefix in NOISE_PREFIXES):
        return True
    return PurePosixPath(name).name in NOISE_NAMES


def _suspicious_ratio(info: zipfile.ZipInfo) -> bool:
    if info.file_size <= 1024 * 1024:
        return False
    compressed = max(1, info.compress_size)
    return info.file_size / compressed > MAX_COMPRESSION_RATIO


async def parse_zip(file_path: str, options: Dict = None) -> ParseResult:
    """Project a ZIP archive to markdown: listing first, then inlined text members."""
    start_time = time.time()
    options = options or {}
    max_file_chars = int(options.get("max_file_chars", MAX_FILE_CHARS) or MAX_FILE_CHARS)
    max_total_chars = int(options.get("max_total_chars", MAX_TOTAL_CHARS) or MAX_TOTAL_CHARS)

    file_id = PurePosixPath(file_path.replace("\\", "/")).stem.split("_")[0]
    # The stored name is "<file_id>.zip"; the caller passes the uploaded name.
    filename = str(options.get("display_name") or PurePosixPath(file_path.replace("\\", "/")).name)

    try:
        archive = zipfile.ZipFile(file_path)
    except zipfile.BadZipFile as exc:
        return ParseResult(
            success=False,
            content_type=ZIP_MIME_TYPE,
            file_id=file_id,
            filename=filename,
            error=f"Not a valid zip archive: {exc}",
            parse_time_ms=int((time.time() - start_time) * 1000),
        )

    extracted_at = datetime.now().isoformat()
    blocks: List[Block] = []
    skipped: List[Dict[str, str]] = []
    listing_lines: List[str] = []
    total_read = 0
    total_chars = 0
    included = 0
    file_index = 0

    with archive:
        members = [info for info in archive.infolist() if not info.is_dir() and not _is_noise(info.filename)]
        listed = members[:MAX_LISTED_ENTRIES]
        for info in listed:
            listing_lines.append(f"- {info.filename} ({_human_size(info.file_size)})")
        if len(members) > MAX_LISTED_ENTRIES:
            listing_lines.append(f"- ... and {len(members) - MAX_LISTED_ENTRIES} more entries")

        listing_text = "\n".join(listing_lines) if listing_lines else "(empty archive)"
        blocks.append(
            Block(
                chunk_id=f"{file_id}_zip_1_0",
                type="list",
                content=f"Archive {filename}: {len(members)} file(s)\n{listing_text}",
                page=1,
                method="zipfile",
                confidence=1.0,
                extracted_at=extracted_at,
            )
        )

        for info in listed:
            name = info.filename
            ext = _entry_extension(name)
            if ext in ARCHIVE_ENTRY_EXTENSIONS:
                skipped.append({"name": name, "reason": "nested archive"})
                continue
            if included >= MAX_TEXT_FILES:
                skipped.append({"name": name, "reason": "file budget reached"})
                continue
            if total_chars >= max_total_chars:
                skipped.append({"name": name, "reason": "text budget reached"})
                continue
            if info.flag_bits & 0x1:
                skipped.append({"name": name, "reason": "encrypted"})
                continue
            if info.file_size > MAX_ENTRY_BYTES or _suspicious_ratio(info):
                skipped.append({"name": name, "reason": "too large to inline"})
                continue
            if total_read + info.file_size > MAX_TOTAL_READ_BYTES:
                skipped.append({"name": name, "reason": "archive read budget reached"})
                continue

            try:
                with archive.open(info) as handle:
                    data = handle.read(MAX_ENTRY_BYTES + 1)
            except Exception as exc:  # corrupt member, unsupported compression, ...
                skipped.append({"name": name, "reason": f"unreadable ({exc.__class__.__name__})"})
                continue
            total_read += len(data)
            if len(data) > MAX_ENTRY_BYTES:
                skipped.append({"name": name, "reason": "too large to inline"})
                continue

            known_text = ext in TEXT_ENTRY_EXTENSIONS
            if not known_text and not looks_like_text(data[:4096]):
                skipped.append({"name": name, "reason": "binary"})
                continue
            if known_text and data and not looks_like_text(data[:4096]):
                skipped.append({"name": name, "reason": "not UTF-8 or GB18030 text"})
                continue

            decoded, _encoding = decode_text_bytes(data)
            text = decoded.replace("\r\n", "\n").replace("\r", "\n")
            truncated = False
            if len(text) > max_file_chars:
                text = text[:max_file_chars]
                truncated = True
            remaining = max_total_chars - total_chars
            if len(text) > remaining:
                text = text[:remaining]
                truncated = True
            if not text.strip():
                skipped.append({"name": name, "reason": "empty"})
                continue

            included += 1
            file_index += 1
            total_chars += len(text)
            page = file_index + 1  # page 1 is the listing
            blocks.append(
                Block(
                    chunk_id=f"{file_id}_zip_{page}_0",
                    type="heading",
                    content=name + (" (truncated)" if truncated else ""),
                    level=2,
                    sheet=name,
                    page=page,
                    method="zipfile",
                    confidence=1.0,
                    extracted_at=extracted_at,
                )
            )
            for para_index, paragraph in enumerate(_split_paragraphs(text), 1):
                blocks.append(
                    Block(
                        chunk_id=f"{file_id}_zip_{page}_{para_index}",
                        type="paragraph",
                        content=paragraph,
                        markdown=paragraph,
                        sheet=name,
                        page=page,
                        method="zipfile",
                        confidence=1.0,
                        extracted_at=extracted_at,
                    )
                )

    markdown = _blocks_to_markdown(filename, blocks, skipped)
    return ParseResult(
        success=True,
        content_type=ZIP_MIME_TYPE,
        file_id=file_id,
        filename=filename,
        markdown=markdown,
        blocks=blocks,
        json={
            "entries": len(members),
            "listed": len(listed),
            "text_files": included,
            "total_chars": total_chars,
            "skipped": skipped[:50],
        },
        parse_time_ms=int((time.time() - start_time) * 1000),
    )


def _blocks_to_markdown(filename: str, blocks: List[Block], skipped: List[Dict[str, str]]) -> str:
    parts: List[str] = [f"# Archive: {filename}\n"]
    current: Optional[str] = None
    body: List[str] = []

    def flush() -> None:
        nonlocal current, body
        if current is not None:
            parts.append(f"\n## {current}\n\n```\n" + "\n\n".join(body) + "\n```\n")
        current = None
        body = []

    for block in blocks:
        if block.type == "list":
            parts.append(block.content + "\n")
        elif block.type == "heading":
            flush()
            current = block.content
        elif block.type == "paragraph":
            body.append(block.content)
    flush()

    if skipped:
        parts.append("\nNot inlined:\n")
        for item in skipped[:50]:
            parts.append(f"- {item['name']}: {item['reason']}")
        if len(skipped) > 50:
            parts.append(f"- ... and {len(skipped) - 50} more")
    return "\n".join(parts).strip() + "\n"
