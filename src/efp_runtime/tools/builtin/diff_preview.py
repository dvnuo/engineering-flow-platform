"""Bounded text and unified diff previews for built-in tools."""

from __future__ import annotations

import difflib
from typing import Any


DEFAULT_MAX_PREVIEW_LINES = 200
DEFAULT_MAX_PREVIEW_CHARS = 12000
DEFAULT_TEXT_PREVIEW_CHARS = 120


def bounded_text_preview(text: str, max_lines: int, max_chars: int) -> tuple[str, bool]:
    """Return a head preview bounded by line and character counts."""

    _validate_non_negative_int("max_lines", max_lines)
    _validate_non_negative_int("max_chars", max_chars)

    preview = text
    truncated = False

    lines = text.splitlines(keepends=True)
    if len(lines) > max_lines:
        preview = "".join(lines[:max_lines]) if max_lines > 0 else ""
        truncated = True

    if len(preview) > max_chars:
        preview = preview[:max_chars] if max_chars > 0 else ""
        truncated = True

    return preview, truncated


def unified_diff_preview(
    old: str,
    new: str,
    fromfile: str,
    tofile: str,
    max_lines: int,
    max_chars: int,
) -> tuple[str, bool]:
    """Return a bounded unified diff preview for two text values."""

    diff_lines = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=fromfile,
        tofile=tofile,
        lineterm="",
    )
    diff_text = "\n".join(diff_lines)
    if diff_text:
        diff_text = f"{diff_text}\n"
    return bounded_text_preview(diff_text, max_lines, max_chars)


def text_diff_stats(old: str, new: str) -> tuple[int, int]:
    """Return added and removed line counts for two text values."""

    matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines())
    additions = 0
    deletions = 0
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            deletions += old_end - old_start
        if tag in {"replace", "insert"}:
            additions += new_end - new_start
    return additions, deletions


def file_diff_record(
    *,
    path: str,
    old_text: str,
    new_text: str,
    patch: str,
    old_path: str | None = None,
) -> dict[str, Any]:
    """Return structured file-level diff metadata."""

    additions, deletions = text_diff_stats(old_text, new_text)
    return {
        "path": path,
        "old_path": old_path or path,
        "additions": additions,
        "deletions": deletions,
        "patch": patch,
    }


def text_preview(value: str, max_chars: int = DEFAULT_TEXT_PREVIEW_CHARS) -> str:
    """Return a single-line, bounded preview for diagnostics."""

    _validate_non_negative_int("max_chars", max_chars)
    preview = (
        str(value)
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    if len(preview) <= max_chars:
        return preview
    if max_chars <= 3:
        return preview[:max_chars]
    return f"{preview[: max_chars - 3]}..."


def _validate_non_negative_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0.")
