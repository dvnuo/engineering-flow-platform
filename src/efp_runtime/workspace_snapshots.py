"""Persistent workspace file snapshots for Runtime v2."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import difflib
import hashlib
import json
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
_RUNTIME_DIR_NAME = ".efp_runtime"
_SNAPSHOT_DIR_NAME = "workspace_snapshots"
_SNAPSHOT_FILES_DIR_NAME = "files"
_SNAPSHOT_MANIFEST_NAME = "manifest.json"
_SNAPSHOT_ID_PREFIX = "workspace_snapshot_"


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
    """Capture, diff, and restore regular workspace files with disk backing."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.storage_root = (
            self.workspace_root / _RUNTIME_DIR_NAME / _SNAPSHOT_DIR_NAME
        )
        self._snapshots: dict[str, _SnapshotRecord] = {}
        self._next_snapshot_index = 1
        self._lock = RLock()
        self._load_existing_snapshots()

    def create_snapshot(
        self,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkspaceSnapshot:
        with self._lock:
            files = self._scan_workspace()
            snapshot_id = self._allocate_snapshot_id()
            snapshot = WorkspaceSnapshot(
                snapshot_id=snapshot_id,
                workspace_root=self.workspace_root,
                created_at=utc_now_iso(),
                label=label,
                metadata=dict(metadata or {}),
                file_count=len(files),
                total_bytes=sum(item.size for item in files.values()),
            )
            record = _SnapshotRecord(
                snapshot=deepcopy(snapshot),
                files=dict(files),
            )
            self._persist_record(record)
            self._snapshots[snapshot.snapshot_id] = record
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
            record = self._require_snapshot(snapshot_id)
            shutil.rmtree(
                self._snapshot_dir(record.snapshot.snapshot_id),
                ignore_errors=True,
            )
            del self._snapshots[snapshot_id]
            return True

    def _require_snapshot(self, snapshot_id: str) -> _SnapshotRecord:
        try:
            return self._snapshots[snapshot_id]
        except KeyError as exc:
            raise KeyError(f"unknown workspace snapshot: {snapshot_id}") from exc

    def _allocate_snapshot_id(self) -> str:
        while True:
            snapshot_id = f"{_SNAPSHOT_ID_PREFIX}{self._next_snapshot_index}"
            self._next_snapshot_index += 1
            if snapshot_id in self._snapshots:
                continue
            if self._snapshot_dir(snapshot_id).exists():
                continue
            return snapshot_id

    def _load_existing_snapshots(self) -> None:
        if not self.storage_root.is_dir():
            return

        highest_index = 0
        for snapshot_dir in sorted(
            (
                path
                for path in self.storage_root.iterdir()
                if not path.is_symlink() and path.is_dir()
            ),
            key=lambda path: _snapshot_sort_key(path.name),
        ):
            record = self._load_snapshot_record(snapshot_dir)
            if record is None:
                continue
            self._snapshots[record.snapshot.snapshot_id] = record
            index = _snapshot_index(record.snapshot.snapshot_id)
            if index is not None:
                highest_index = max(highest_index, index)

        self._next_snapshot_index = max(self._next_snapshot_index, highest_index + 1)

    def _load_snapshot_record(self, snapshot_dir: Path) -> _SnapshotRecord | None:
        manifest_path = snapshot_dir / _SNAPSHOT_MANIFEST_NAME
        if not manifest_path.is_file():
            return None

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None

        snapshot_id = payload.get("snapshot_id")
        if not isinstance(snapshot_id, str) or snapshot_id != snapshot_dir.name:
            return None
        try:
            self._snapshot_dir(snapshot_id)
        except ValueError:
            return None

        manifest_root = payload.get("workspace_root")
        if isinstance(manifest_root, str):
            try:
                if Path(manifest_root).expanduser().resolve() != self.workspace_root:
                    return None
            except OSError:
                return None

        files = self._load_snapshot_files(snapshot_dir)
        file_count = _safe_nonnegative_int(payload.get("file_count"))
        total_bytes = _safe_nonnegative_int(payload.get("total_bytes"))
        if file_count is not None and file_count != len(files):
            return None
        calculated_total_bytes = sum(item.size for item in files.values())
        if total_bytes is not None and total_bytes != calculated_total_bytes:
            return None

        metadata = payload.get("metadata")
        label = payload.get("label")
        snapshot = WorkspaceSnapshot(
            snapshot_id=snapshot_id,
            workspace_root=self.workspace_root,
            created_at=payload.get("created_at")
            if isinstance(payload.get("created_at"), str)
            else utc_now_iso(),
            label=label if isinstance(label, str) else None,
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            file_count=len(files),
            total_bytes=calculated_total_bytes,
        )
        return _SnapshotRecord(snapshot=snapshot, files=files)

    def _load_snapshot_files(self, snapshot_dir: Path) -> dict[str, _CapturedFile]:
        files_dir = snapshot_dir / _SNAPSHOT_FILES_DIR_NAME
        files: dict[str, _CapturedFile] = {}
        if not files_dir.is_dir():
            return files
        self._load_snapshot_files_directory(files_dir, (), files)
        return files

    def _load_snapshot_files_directory(
        self,
        directory: Path,
        relative_parts: tuple[str, ...],
        files: dict[str, _CapturedFile],
    ) -> None:
        with os.scandir(directory) as entries:
            sorted_entries = sorted(entries, key=lambda entry: entry.name)

            for entry in sorted_entries:
                if entry.is_symlink():
                    continue

                path = Path(entry.path)
                child_parts = (*relative_parts, entry.name)
                if entry.is_dir(follow_symlinks=False):
                    self._load_snapshot_files_directory(path, child_parts, files)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue

                relative_path = "/".join(child_parts)
                self._validate_relative_path(relative_path)
                content = path.read_bytes()
                files[relative_path] = _CapturedFile(
                    path=relative_path,
                    content=content,
                    sha256=hashlib.sha256(content).hexdigest(),
                    size=len(content),
                )

    def _persist_record(self, record: _SnapshotRecord) -> None:
        snapshot_dir = self._snapshot_dir(record.snapshot.snapshot_id)
        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        files_dir = snapshot_dir / _SNAPSHOT_FILES_DIR_NAME
        files_dir.mkdir(parents=True, exist_ok=False)

        for relative_path, captured in sorted(record.files.items()):
            path = self._snapshot_file_path(files_dir, relative_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(captured.content)

        manifest = {
            "snapshot_id": record.snapshot.snapshot_id,
            "workspace_root": str(record.snapshot.workspace_root),
            "created_at": record.snapshot.created_at,
            "label": record.snapshot.label,
            "metadata": record.snapshot.metadata,
            "file_count": record.snapshot.file_count,
            "total_bytes": record.snapshot.total_bytes,
        }
        (snapshot_dir / _SNAPSHOT_MANIFEST_NAME).write_text(
            json.dumps(manifest, default=str, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _snapshot_dir(self, snapshot_id: str) -> Path:
        if (
            not snapshot_id
            or snapshot_id in {".", ".."}
            or "/" in snapshot_id
            or "\\" in snapshot_id
        ):
            raise ValueError(f"invalid workspace snapshot id: {snapshot_id}")
        return self.storage_root / snapshot_id

    def _snapshot_file_path(self, files_dir: Path, relative_path: str) -> Path:
        posix_path = self._validate_relative_path(relative_path)
        return files_dir.joinpath(*posix_path.parts)

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
        posix_path = self._validate_relative_path(relative_path)
        return self.workspace_root.joinpath(*posix_path.parts)

    def _validate_relative_path(self, relative_path: str) -> PurePosixPath:
        posix_path = PurePosixPath(relative_path)
        if posix_path.is_absolute():
            raise ValueError(f"workspace-relative path is absolute: {relative_path}")
        parts = posix_path.parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"invalid workspace-relative path: {relative_path}")
        return posix_path

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


def _snapshot_index(snapshot_id: str) -> int | None:
    if not snapshot_id.startswith(_SNAPSHOT_ID_PREFIX):
        return None
    suffix = snapshot_id.removeprefix(_SNAPSHOT_ID_PREFIX)
    if not suffix.isdigit():
        return None
    return int(suffix)


def _snapshot_sort_key(snapshot_id: str) -> tuple[int, int | str]:
    index = _snapshot_index(snapshot_id)
    if index is not None:
        return (0, index)
    return (1, snapshot_id)


def _safe_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


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
