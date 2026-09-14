"""Detect files an agent produced under the workspace deliverables directory.

A member who asks for "a PPT" wants a file, not a description of one. The
agent writes it under ``output/`` (configurable with ``EFP_DELIVERABLES_DIR``);
this module compares that directory before and after a chat turn and turns
every new or changed file into a ``file`` display block. Portal renders those
blocks as download cards under the assistant's reply, in the live stream and
again when the session history is reloaded.

Nothing here may fail a chat turn: every public function swallows filesystem
errors and returns an empty result instead.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import logging
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

DELIVERABLES_DIR_ENV = "EFP_DELIVERABLES_DIR"
DEFAULT_DELIVERABLES_DIR = "output"
MAX_FILES_ENV = "EFP_DELIVERABLES_MAX_FILES"
DEFAULT_MAX_FILES = 20
# Bound the walk so a workspace with a huge output/ tree cannot stall a turn.
MAX_SCAN_ENTRIES = 5000

FILE_BLOCK_TYPE = "file"
# Message metadata key the file blocks are persisted under, read back by the
# session facade when history is loaded.
DELIVERABLE_BLOCKS_METADATA_KEY = "deliverable_blocks"

_CONTENT_TYPE_OVERRIDES = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".potx": "application/vnd.openxmlformats-officedocument.presentationml.template",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}

Snapshot = dict[str, tuple[int, int]]


def deliverables_dir_name() -> str:
    """Workspace-relative directory watched for deliverables (default ``output``)."""
    raw = str(os.getenv(DELIVERABLES_DIR_ENV, "") or "").strip().replace("\\", "/")
    if not raw:
        return DEFAULT_DELIVERABLES_DIR
    candidate = PurePosixPath(raw.rstrip("/"))
    escapes = (
        raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw) is not None
        or not candidate.parts
        or any(part in {".", ".."} for part in candidate.parts)
    )
    if escapes:
        logger.warning("Ignoring invalid %s=%r; using %r", DELIVERABLES_DIR_ENV, raw, DEFAULT_DELIVERABLES_DIR)
        return DEFAULT_DELIVERABLES_DIR
    return candidate.as_posix()


def max_deliverable_files() -> int:
    raw = os.getenv(MAX_FILES_ENV, "")
    try:
        value = int(str(raw).strip()) if str(raw).strip() else DEFAULT_MAX_FILES
    except ValueError:
        value = DEFAULT_MAX_FILES
    return value if value > 0 else DEFAULT_MAX_FILES


def deliverables_root(workspace_root: str | Path) -> Path:
    return Path(workspace_root) / deliverables_dir_name()


def snapshot_deliverables(workspace_root: str | Path | None) -> Snapshot:
    """Map workspace-relative posix path -> (mtime_ns, size) for every regular file."""
    if workspace_root is None:
        return {}
    root = Path(workspace_root)
    watched = deliverables_root(root)
    snapshot: Snapshot = {}
    try:
        if not watched.is_dir() or watched.is_symlink():
            return snapshot
        _walk(watched, root, snapshot)
    except OSError as exc:
        logger.debug("Deliverables snapshot skipped for %s: %s", watched, exc)
    return snapshot


def _walk(directory: Path, workspace_root: Path, snapshot: Snapshot) -> None:
    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if len(snapshot) >= MAX_SCAN_ENTRIES:
                return
            name = entry.name
            if name.startswith("."):
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            relative = Path(entry.path).relative_to(workspace_root).as_posix()
            snapshot[relative] = (int(stat.st_mtime_ns), int(stat.st_size))


def diff_deliverables(before: Mapping[str, tuple[int, int]], after: Mapping[str, tuple[int, int]]) -> list[tuple[str, str]]:
    """Return ``(relative_path, action)`` pairs for files that appeared or changed, newest first."""
    changed: list[tuple[str, str, int]] = []
    for path, (mtime_ns, size) in after.items():
        previous = before.get(path)
        if previous is None:
            changed.append((path, "created", mtime_ns))
        elif previous != (mtime_ns, size):
            changed.append((path, "updated", mtime_ns))
    changed.sort(key=lambda item: (-item[2], item[0]))
    limit = max_deliverable_files()
    if len(changed) > limit:
        logger.info("Deliverables: %d changed files, reporting the newest %d", len(changed), limit)
    return [(path, action) for path, action, _ in changed[:limit]]


def guess_content_type(name: str) -> str:
    suffix = PurePosixPath(name).suffix.lower()
    override = _CONTENT_TYPE_OVERRIDES.get(suffix)
    if override:
        return override
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def build_file_display_block(workspace_root: str | Path, relative_path: str, *, action: str = "created") -> dict[str, Any]:
    """One ``file`` display block; Portal needs ``path`` (workspace-relative) and ``name``."""
    posix_path = PurePosixPath(relative_path)
    block: dict[str, Any] = {
        "type": FILE_BLOCK_TYPE,
        "path": posix_path.as_posix(),
        "name": posix_path.name,
        "content_type": guess_content_type(posix_path.name),
        "action": action,
    }
    try:
        stat = (Path(workspace_root) / Path(*posix_path.parts)).stat()
        block["size"] = int(stat.st_size)
        block["modified_at"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        pass
    return block


def build_deliverable_display_blocks(
    workspace_root: str | Path | None,
    before: Mapping[str, tuple[int, int]],
    after: Mapping[str, tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    """File blocks for everything under the deliverables dir that this turn created or changed."""
    if workspace_root is None:
        return []
    try:
        current = snapshot_deliverables(workspace_root) if after is None else after
        return [
            build_file_display_block(workspace_root, path, action=action)
            for path, action in diff_deliverables(before, current)
        ]
    except Exception as exc:  # pragma: no cover - defensive: never fail the turn
        logger.warning("Deliverables detection failed: %s", exc)
        return []


def compose_display_blocks(text: str | None, file_blocks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Markdown block for the reply text (when any) followed by the file blocks."""
    blocks: list[dict[str, Any]] = []
    if isinstance(text, str) and text.strip():
        blocks.append({"type": "markdown", "content": text})
    blocks.extend(deepcopy(dict(block)) for block in file_blocks if isinstance(block, Mapping))
    return blocks


def attach_deliverable_blocks(payload: dict[str, Any], file_blocks: list[dict[str, Any]]) -> None:
    """Put the file blocks on a chat result payload without losing the reply text.

    ``display_blocks`` replaces the markdown rendering in Portal, so the reply
    text must travel along as the first block.
    """
    if not file_blocks:
        return
    existing = payload.get("display_blocks")
    if isinstance(existing, list) and existing:
        payload["display_blocks"] = list(existing) + [deepcopy(block) for block in file_blocks]
        return
    text = payload.get("response") if isinstance(payload.get("response"), str) else payload.get("content")
    payload["display_blocks"] = compose_display_blocks(text if isinstance(text, str) else "", file_blocks)


def persist_deliverable_blocks(store: Any, session_id: str, message_id: str | None, file_blocks: list[dict[str, Any]]) -> bool:
    """Record the file blocks on the assistant message so history reloads show the same cards."""
    if not file_blocks or not message_id:
        return False
    try:
        history = list(store.read_history(session_id))
    except Exception as exc:
        logger.warning("Deliverables: could not read history for %s: %s", session_id, exc)
        return False
    target = next((message for message in reversed(history) if getattr(message, "message_id", None) == message_id), None)
    if target is None:
        return False
    target.metadata[DELIVERABLE_BLOCKS_METADATA_KEY] = [deepcopy(block) for block in file_blocks]
    try:
        store.replace_history(session_id, history)
    except Exception as exc:
        logger.warning("Deliverables: could not persist file blocks for %s: %s", session_id, exc)
        return False
    return True
