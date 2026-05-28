"""Archived truncation for model-visible tool output."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import time
from typing import Any, Optional, Union
from uuid import uuid4


DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024


@dataclass(frozen=True)
class TruncationLimits:
    """Line/byte limits for archived tool output truncation."""

    max_lines: Optional[int] = DEFAULT_MAX_LINES
    max_bytes: Optional[int] = DEFAULT_MAX_BYTES
    direction: str = "head"


@dataclass(frozen=True)
class TruncationResult:
    """Result of applying archived output truncation."""

    content: str
    truncated: bool
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolOutputTruncator:
    """Persist complete tool output and return a bounded model-visible preview."""

    def __init__(
        self,
        output_dir: Union[str, Path],
        limits: Optional[TruncationLimits] = None,
        archive_full_output: bool = True,
        task_hint_enabled: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.limits = limits or TruncationLimits()
        self.archive_full_output = bool(archive_full_output)
        self.task_hint_enabled = bool(task_hint_enabled)

    def truncate(
        self,
        text: str,
        *,
        limits: Optional[TruncationLimits] = None,
        allow_archive: bool = True,
    ) -> TruncationResult:
        active_limits = limits or self.limits
        _validate_limits(active_limits)

        original_bytes = text.encode("utf-8")
        original_lines = text.splitlines()
        metadata = _base_metadata(
            text,
            original_bytes=len(original_bytes),
            original_lines=len(original_lines),
        )

        truncated_by = _truncation_reasons(
            line_count=len(original_lines),
            byte_count=len(original_bytes),
            limits=active_limits,
        )
        if not truncated_by:
            metadata["truncated"] = False
            return TruncationResult(content=text, truncated=False, metadata=metadata)

        preview = _preview_text(text, limits=active_limits)
        preview_bytes = len(preview.encode("utf-8"))
        preview_lines = len(preview.splitlines())
        removed = {
            "chars": max(len(text) - len(preview), 0),
            "bytes": max(len(original_bytes) - preview_bytes, 0),
            "lines": max(len(original_lines) - preview_lines, 0),
        }

        output_path: Optional[str] = None
        if self.archive_full_output and allow_archive:
            output_path = str(self._write_full_output(text))

        metadata.update(
            {
                "truncated": True,
                "truncated_by": truncated_by,
                "removed": removed,
            }
        )
        if output_path is not None:
            metadata["output_path"] = output_path

        content = _format_truncated_content(
            preview=preview,
            removed=removed,
            output_path=output_path,
            direction=_normalize_direction(active_limits.direction),
            task_hint_enabled=self.task_hint_enabled,
        )
        return TruncationResult(content=content, truncated=True, metadata=metadata)

    def cleanup(self, retention_seconds: Optional[int] = None) -> int:
        """Remove archived outputs older than retention_seconds."""

        if (
            retention_seconds is None
            or retention_seconds <= 0
            or not self.output_dir.exists()
        ):
            return 0
        cutoff = time.time() - retention_seconds
        removed = 0
        for path in self.output_dir.glob("tool_*.txt"):
            try:
                if not path.is_file() or path.stat().st_mtime > cutoff:
                    continue
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def _write_full_output(self, text: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        path = self.output_dir / f"tool_{timestamp}_{uuid4().hex[:12]}.txt"
        path.write_text(text, encoding="utf-8")
        return path


def _base_metadata(
    text: str,
    *,
    original_bytes: Optional[int] = None,
    original_lines: Optional[int] = None,
) -> dict[str, Any]:
    return {
        "original_chars": len(text),
        "original_bytes": (
            len(text.encode("utf-8")) if original_bytes is None else original_bytes
        ),
        "original_lines": (
            len(text.splitlines()) if original_lines is None else original_lines
        ),
    }


def _validate_limits(limits: TruncationLimits) -> None:
    if limits.max_lines is not None and limits.max_lines < 0:
        raise ValueError("max_lines must be greater than or equal to 0 or None")
    if limits.max_bytes is not None and limits.max_bytes < 0:
        raise ValueError("max_bytes must be greater than or equal to 0 or None")
    _normalize_direction(limits.direction)


def _normalize_direction(direction: str) -> str:
    if direction not in ("head", "tail"):
        raise ValueError("truncation direction must be 'head' or 'tail'")
    return direction


def _truncation_reasons(
    *,
    line_count: int,
    byte_count: int,
    limits: TruncationLimits,
) -> list[str]:
    reasons: list[str] = []
    if limits.max_lines is not None and line_count > limits.max_lines:
        reasons.append("lines")
    if limits.max_bytes is not None and byte_count > limits.max_bytes:
        reasons.append("bytes")
    return reasons


def _preview_text(text: str, *, limits: TruncationLimits) -> str:
    direction = _normalize_direction(limits.direction)
    preview = _line_limited_preview(
        text,
        max_lines=limits.max_lines,
        direction=direction,
    )
    return _byte_limited_preview(
        preview,
        max_bytes=limits.max_bytes,
        direction=direction,
    )


def _line_limited_preview(
    text: str,
    *,
    max_lines: Optional[int],
    direction: str,
) -> str:
    if max_lines is None:
        return text
    lines = text.splitlines(keepends=True)
    if len(text.splitlines()) <= max_lines:
        return text
    if max_lines <= 0:
        return ""
    if direction == "tail":
        return "".join(lines[-max_lines:])
    return "".join(lines[:max_lines])


def _byte_limited_preview(
    text: str,
    *,
    max_bytes: Optional[int],
    direction: str,
) -> str:
    if max_bytes is None:
        return text
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    if max_bytes <= 0:
        return ""
    if direction == "tail":
        return data[-max_bytes:].decode("utf-8", errors="ignore")
    return data[:max_bytes].decode("utf-8", errors="ignore")


def _format_truncated_content(
    *,
    preview: str,
    removed: dict[str, int],
    output_path: Optional[str],
    direction: str,
    task_hint_enabled: bool,
) -> str:
    notice = (
        f"...{removed['lines']} lines/{removed['bytes']} bytes truncated..."
    )
    if output_path is None:
        hint = (
            "Full tool output was not archived. Re-run with narrower tool "
            "arguments to inspect specific sections."
        )
    elif task_hint_enabled:
        hint = (
            f"Full tool output was saved to {output_path}. Use the Task tool "
            "to have an explore or research subagent inspect the saved file "
            "with grep and read using offset/limit. Do not read the entire "
            "file at once."
        )
    else:
        hint = (
            f"Full tool output was saved to {output_path}. Use read_file or "
            "grep to inspect specific sections; avoid reading the entire large "
            "file at once."
        )

    preview = preview.rstrip("\n")
    if direction == "tail":
        parts = [notice, hint]
        if preview:
            parts.append(preview)
        return "\n".join(parts)

    parts = []
    if preview:
        parts.append(preview)
    parts.extend([notice, hint])
    return "\n".join(parts)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "ToolOutputTruncator",
    "TruncationLimits",
    "TruncationResult",
]
