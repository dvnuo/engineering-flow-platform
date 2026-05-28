"""In-memory workspace file snapshots for Runtime v2."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import difflib
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
from threading import RLock
from typing import Any, Literal

from .types import utc_now_iso


_EXCLUDED_NAMES = {
    ".efp_runtime",
    ".git",
    ".pytest_cache",
    "__pycache__",
}


@dataclass
class WorkspaceSnapshot:
    snapshot_id: str
    workspace_root: Path
    created_at: str
    label: str | None
    metadata: dict[str, Any]
    file_count: int
    total_bytes: int

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root)
        self.metadata = dict(self.metadata)


@dataclass
class WorkspaceSnapshotDiff:
    path: str
    status: Literal["added", "deleted", "modified"]
    before_hash: str | None
    after_hash: str | None
    before_bytes: int | None
    after_bytes: int | None
    patch: str | None
    additions: int
    deletions: int


@dataclass(frozen=True)
class _CapturedFile:
    path: str
    content: bytes
    sha256: str
    size: int


@dataclass
class _SnapshotRecord:
    snapshot: WorkspaceSnapshot
    files: dict[str, _CapturedFile] = field(default_factory=dict)


class WorkspaceSnapshotStore:
    """Capture, diff, and restore regular workspace files in memory."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self._snapshots: dict[str, _SnapshotRecord] = {}
        self._next_snapshot_index = 1
        self._lock = RLock()

    def create_snapshot(
        self,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkspaceSnapshot:
        with self._lock:
            files = self._scan_workspace()
            snapshot_id = f"workspace_snapshot_{self._next_snapshot_index}"
            self._next_snapshot_index += 1
            snapshot = WorkspaceSnapshot(
                snapshot_id=snapshot_id,
                workspace_root=self.workspace_root,
                created_at=utc_now_iso(),
                label=label,
                metadata=dict(metadata or {}),
                file_count=len(files),
                total_bytes=sum(item.size for item in files.values()),
            )
            self._snapshots[snapshot.snapshot_id] = _SnapshotRecord(
                snapshot=deepcopy(snapshot),
                files=dict(files),
            )
            return deepcopy(snapshot)

    def list_snapshots(self) -> list[WorkspaceSnapshot]:
        with self._lock:
            return [deepcopy(record.snapshot) for record in self._snapshots.values()]

    def diff_snapshot(self, snapshot_id: str) -> list[WorkspaceSnapshotDiff]:
        with self._lock:
            record = self._require_snapshot(snapshot_id)
            current_files = self._scan_workspace()
            return _diff_files(record.files, current_files)

    def restore_snapshot(
        self,
        snapshot_id: str,
        *,
        delete_added: bool = True,
    ) -> WorkspaceSnapshot:
        with self._lock:
            record = self._require_snapshot(snapshot_id)
            current_files = self._scan_workspace()

            for relative_path, captured in record.files.items():
                self._write_file(relative_path, captured.content)

            if delete_added:
                snapshot_paths = set(record.files)
                for relative_path in sorted(set(current_files) - snapshot_paths):
                    self._delete_regular_file(relative_path)

            return deepcopy(record.snapshot)

    def delete_snapshot(self, snapshot_id: str) -> bool:
        with self._lock:
            if snapshot_id not in self._snapshots:
                return False
            del self._snapshots[snapshot_id]
            return True

    def _require_snapshot(self, snapshot_id: str) -> _SnapshotRecord:
        try:
            return self._snapshots[snapshot_id]
        except KeyError as exc:
            raise KeyError(f"unknown workspace snapshot: {snapshot_id}") from exc

    def _scan_workspace(self) -> dict[str, _CapturedFile]:
        files: dict[str, _CapturedFile] = {}
        self._scan_directory(self.workspace_root, (), files)
        return files

    def _scan_directory(
        self,
        directory: Path,
        relative_parts: tuple[str, ...],
        files: dict[str, _CapturedFile],
    ) -> None:
        with os.scandir(directory) as entries:
            sorted_entries = sorted(entries, key=lambda entry: entry.name)

            for entry in sorted_entries:
                name = entry.name
                if name in _EXCLUDED_NAMES:
                    continue
                if entry.is_symlink():
                    continue

                path = Path(entry.path)
                child_parts = (*relative_parts, name)
                if entry.is_dir(follow_symlinks=False):
                    self._scan_directory(path, child_parts, files)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue

                content = path.read_bytes()
                relative_path = "/".join(child_parts)
                files[relative_path] = _CapturedFile(
                    path=relative_path,
                    content=content,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size=len(content),
                )

    def _workspace_path(self, relative_path: str) -> Path:
        posix_path = PurePosixPath(relative_path)
        if posix_path.is_absolute():
            raise ValueError(f"workspace-relative path is absolute: {relative_path}")
        parts = posix_path.parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"invalid workspace-relative path: {relative_path}")
        return self.workspace_root.joinpath(*parts)

    def _write_file(self, relative_path: str, content: bytes) -> None:
        path = self._workspace_path(relative_path)
        self._ensure_directory(path.parent)
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        path.write_bytes(content)

    def _ensure_directory(self, directory: Path) -> None:
        if directory == self.workspace_root:
            return
        relative = directory.relative_to(self.workspace_root)
        current = self.workspace_root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                current.unlink()
            if current.exists():
                if current.is_dir():
                    continue
                current.unlink()
            current.mkdir()

    def _delete_regular_file(self, relative_path: str) -> None:
        path = self._workspace_path(relative_path)
        if path.is_symlink() or not path.exists() or not path.is_file():
            return
        path.unlink()


def _diff_files(
    before_files: Mapping[str, _CapturedFile],
    after_files: Mapping[str, _CapturedFile],
) -> list[WorkspaceSnapshotDiff]:
    diffs: list[WorkspaceSnapshotDiff] = []
    for path in sorted(set(before_files) | set(after_files)):
        before = before_files.get(path)
        after = after_files.get(path)
        if before is None and after is not None:
            patch, additions, deletions = _unified_patch(path, None, after)
            diffs.append(
                WorkspaceSnapshotDiff(
                    path=path,
                    status="added",
                    before_hash=None,
                    after_hash=after.sha256,
                    before_bytes=None,
                    after_bytes=after.size,
                    patch=patch,
                    additions=additions,
                    deletions=deletions,
                )
            )
            continue
        if before is not None and after is None:
            patch, additions, deletions = _unified_patch(path, before, None)
            diffs.append(
                WorkspaceSnapshotDiff(
                    path=path,
                    status="deleted",
                    before_hash=before.sha256,
                    after_hash=None,
                    before_bytes=before.size,
                    after_bytes=None,
                    patch=patch,
                    additions=additions,
                    deletions=deletions,
                )
            )
            continue
        if before is None or after is None or before.sha256 == after.sha256:
            continue

        patch, additions, deletions = _unified_patch(path, before, after)
        diffs.append(
            WorkspaceSnapshotDiff(
                path=path,
                status="modified",
                before_hash=before.sha256,
                after_hash=after.sha256,
                before_bytes=before.size,
                after_bytes=after.size,
                patch=patch,
                additions=additions,
                deletions=deletions,
            )
        )
    return diffs


def _unified_patch(
    path: str,
    before: _CapturedFile | None,
    after: _CapturedFile | None,
) -> tuple[str | None, int, int]:
    before_lines = _diff_lines(before.content if before is not None else b"")
    after_lines = _diff_lines(after.content if after is not None else b"")
    fromfile = f"a/{path}" if before is not None else "/dev/null"
    tofile = f"b/{path}" if after is not None else "/dev/null"
    lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    if not lines:
        return None, 0, 0

    additions = sum(
        1 for line in lines if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1 for line in lines if line.startswith("-") and not line.startswith("---")
    )
    return "\n".join(lines), additions, deletions


def _diff_lines(content: bytes) -> list[str]:
    return content.decode("utf-8", errors="replace").splitlines()


__all__ = [
    "WorkspaceSnapshot",
    "WorkspaceSnapshotDiff",
    "WorkspaceSnapshotStore",
]
