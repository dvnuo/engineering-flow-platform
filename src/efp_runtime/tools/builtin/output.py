"""Output helpers for Runtime v2 built-in tools."""

from __future__ import annotations

import re
from pathlib import Path

from ...types import new_id
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path


DEFAULT_MAX_OUTPUT_CHARS = 20000
DEFAULT_MAX_OUTPUT_LINES = 200
TRUNCATION_MARKER = "...output truncated..."
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def truncate_tail(
    content: str,
    *,
    max_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    max_lines: int = DEFAULT_MAX_OUTPUT_LINES,
) -> tuple[str, bool]:
    """Return a deterministic tail slice when content exceeds line or char limits."""

    if max_chars <= 0:
        raise ValueError("max_chars must be greater than 0.")
    if max_lines <= 0:
        raise ValueError("max_lines must be greater than 0.")

    lines = content.splitlines(keepends=True)
    over_lines = len(lines) > max_lines
    over_chars = len(content) > max_chars
    if not over_lines and not over_chars:
        return content, False

    tail = "".join(lines[-max_lines:]) if over_lines else content
    prefix = f"{TRUNCATION_MARKER}\n"
    available = max(max_chars - len(prefix), 0)
    if len(tail) > available:
        tail = tail[-available:] if available else ""

    if available == 0:
        return prefix[:max_chars], True
    return f"{prefix}{tail}", True


def save_workspace_output(
    workspace_root: str | Path,
    content: str,
    *,
    name_hint: str | None = None,
    directory: str = ".efp_runtime/tool-output",
) -> str:
    """Persist full tool output under the workspace and return its relative path."""

    root = normalize_workspace_root(workspace_root)
    output_dir = resolve_workspace_path(root, directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = _safe_output_stem(name_hint)
    output_path = resolve_workspace_path(root, output_dir / f"{stem}.log")
    output_path.write_text(content, encoding="utf-8")
    return workspace_relative_path(root, output_path)


def _safe_output_stem(name_hint: str | None) -> str:
    if name_hint:
        stem = _SAFE_NAME_RE.sub("_", str(name_hint)).strip("._-")
        if stem:
            return stem[:120]
    return new_id("tool_output")
