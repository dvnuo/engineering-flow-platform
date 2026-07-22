"""Persistent workspace file snapshots for EFP runtime.

Memory model: snapshot file *content* lives only on disk (under
``.efp_runtime/workspace_snapshots/<id>/files``). The in-memory store keeps
metadata only (path, sha256, size), so holding many snapshots costs a few KB
each instead of a full copy of the workspace tree. Content is streamed from
disk one file at a time for restore/diff.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
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


_ALWAYS_EXCLUDED_NAMES = {
    ".efp_runtime",
    ".git",
    ".pytest_cache",
    "__pycache__",
}
_EXCLUDED_DIRECTORY_NAMES = {
    # Heavy dependency/build directories: never useful to revert and they
    # dominate tree size (a node_modules alone is routinely hundreds of MB).
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    ".next",
    ".nuxt",
    "dist",
    "build",
    "target",
    "vendor",
    "coverage",
}
_RUNTIME_DIR_NAME = ".efp_runtime"
_SNAPSHOT_DIR_NAME = "workspace_snapshots"
_SNAPSHOT_FILES_DIR_NAME = "files"
_SNAPSHOT_MANIFEST_NAME = "manifest.json"
_SNAPSHOT_ID_PREFIX = "workspace_snapshot_"
_MANIFEST_FORMAT = 2
_HASH_CHUNK_BYTES = 1024 * 1024

# Files larger than this are not captured (recorded as skipped instead).
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
# Oldest snapshots beyond this count are pruned from disk after each capture.
DEFAULT_MAX_RETAINED_SNAPSHOTS = 20

_COORDINATION_LOCK = RLock()
_WORKSPACE_LOCKS: dict[Path, RLock] = {}
_WORKSPACE_PROTECTED_SNAPSHOTS: dict[Path, dict[str, int]] = {}


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
    """Metadata for one captured file; content lives on disk only."""

    path: str
    sha256: str
    size: int


@dataclass
class _SnapshotRecord:
    snapshot: WorkspaceSnapshot
    files: dict[str, _CapturedFile] = field(default_factory=dict)
    # Paths present at capture time but not captured (e.g. above the size
    # cap), mapped to their observed size. Restore must neither recreate nor
    # delete these.
    skipped: dict[str, int] = field(default_factory=dict)


class WorkspaceSnapshotStore:
    """Capture, diff, and restore regular workspace files with disk backing."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_retained_snapshots: int = DEFAULT_MAX_RETAINED_SNAPSHOTS,
        on_snapshot_removed: Callable[[str], None] | None = None,
        retained_snapshot_ids: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.storage_root = (
            self.workspace_root / _RUNTIME_DIR_NAME / _SNAPSHOT_DIR_NAME
        )
        self.max_file_bytes = max(0, int(max_file_bytes))
        self.max_retained_snapshots = max(1, int(max_retained_snapshots))
        self._on_snapshot_removed = on_snapshot_removed
        self._retained_snapshot_ids = retained_snapshot_ids
        self._snapshots: dict[str, _SnapshotRecord] = {}
        self._next_snapshot_index = 1
        self._lock, self._protected_snapshot_ids = _workspace_coordination(
            self.workspace_root
        )
        with self._lock:
            self._load_existing_snapshots()

    def create_snapshot(
        self,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkspaceSnapshot:
        with self._lock:
            self._reload_existing_snapshots()
            snapshot_id = self._allocate_snapshot_id()
            snapshot_dir = self._snapshot_dir(snapshot_id)
            if snapshot_dir.exists():
                shutil.rmtree(snapshot_dir)
            files_dir = snapshot_dir / _SNAPSHOT_FILES_DIR_NAME
            files_dir.mkdir(parents=True, exist_ok=False)

            files: dict[str, _CapturedFile] = {}
            skipped: dict[str, int] = {}
            try:
                self._capture_directory(
                    self.workspace_root, (), files_dir, files, skipped
                )
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
                    files=files,
                    skipped=skipped,
                )
                # Manifest is written last: its presence marks the snapshot
                # as complete (partial dirs without a manifest are ignored
                # by _load_existing_snapshots).
                self._write_manifest(snapshot_dir, record)
            except BaseException:
                shutil.rmtree(snapshot_dir, ignore_errors=True)
                raise

            self._snapshots[snapshot.snapshot_id] = record
            self._prune_old_snapshots(protect={snapshot.snapshot_id})
            return deepcopy(snapshot)

    def list_snapshots(self) -> list[WorkspaceSnapshot]:
        with self._lock:
            self._reload_existing_snapshots()
            return [deepcopy(record.snapshot) for record in self._snapshots.values()]

    @contextmanager
    def protect_snapshot(self, snapshot_id: str) -> Iterator[None]:
        """Keep a snapshot retained for a multi-step operation."""

        with self._lock:
            self._reload_existing_snapshots()
            self._require_snapshot(snapshot_id)
            self._protected_snapshot_ids[snapshot_id] = (
                self._protected_snapshot_ids.get(snapshot_id, 0) + 1
            )
            try:
                yield
            finally:
                remaining = self._protected_snapshot_ids[snapshot_id] - 1
                if remaining:
                    self._protected_snapshot_ids[snapshot_id] = remaining
                else:
                    del self._protected_snapshot_ids[snapshot_id]
                self._prune_old_snapshots(protect=set())

    def diff_snapshot(self, snapshot_id: str) -> list[WorkspaceSnapshotDiff]:
        with self._lock:
            self._reload_existing_snapshots()
            record = self._require_snapshot(snapshot_id)
            self._validate_snapshot_blobs(snapshot_id, record)
            current_files = self._scan_workspace()
            # Files that existed at capture time but were skipped (size cap)
            # have no captured content to compare against.
            for skipped_path in record.skipped:
                current_files.pop(skipped_path, None)
            return _diff_files(
                record.files,
                current_files,
                before_loader=self._snapshot_content_loader(snapshot_id),
                after_loader=self._workspace_content_loader(),
                max_patch_bytes=self.max_file_bytes or None,
            )

    def restore_snapshot(
        self,
        snapshot_id: str,
        *,
        delete_added: bool = True,
    ) -> WorkspaceSnapshot:
        with self._lock:
            self._reload_existing_snapshots()
            record = self._require_snapshot(snapshot_id)
            self._validate_snapshot_blobs(snapshot_id, record)
            load_content = self._snapshot_content_loader(snapshot_id)

            for relative_path in sorted(record.files):
                self._write_file(relative_path, load_content(relative_path))

            if delete_added:
                current_files = self._scan_workspace()
                protected = set(record.files) | set(record.skipped)
                for relative_path in sorted(set(current_files) - protected):
                    self._delete_regular_file(relative_path)

            return deepcopy(record.snapshot)

    def delete_snapshot(self, snapshot_id: str) -> bool:
        with self._lock:
            self._reload_existing_snapshots()
            self._require_snapshot(snapshot_id)
            self._remove_snapshot(snapshot_id)
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

    def _prune_old_snapshots(self, *, protect: set[str]) -> None:
        if len(self._snapshots) <= self.max_retained_snapshots:
            return
        protected = protect | set(self._protected_snapshot_ids)
        if self._retained_snapshot_ids is not None:
            protected.update(self._retained_snapshot_ids())
        ordered = sorted(
            self._snapshots,
            key=lambda snapshot_id: _snapshot_sort_key(snapshot_id),
        )
        excess = len(self._snapshots) - self.max_retained_snapshots
        for snapshot_id in ordered:
            if excess <= 0:
                break
            if snapshot_id in protected:
                continue
            self._remove_snapshot(snapshot_id)
            excess -= 1

    def _remove_snapshot(self, snapshot_id: str) -> None:
        if self._on_snapshot_removed is not None:
            self._on_snapshot_removed(snapshot_id)
        shutil.rmtree(self._snapshot_dir(snapshot_id), ignore_errors=True)
        del self._snapshots[snapshot_id]

    def _validate_snapshot_blobs(
        self,
        snapshot_id: str,
        record: _SnapshotRecord,
    ) -> None:
        files_dir = self._snapshot_dir(snapshot_id) / _SNAPSHOT_FILES_DIR_NAME
        for relative_path, captured in sorted(record.files.items()):
            blob_path = self._snapshot_file_path(files_dir, relative_path)
            try:
                sha256, size = _stream_sha256(blob_path)
            except OSError as exc:
                raise OSError(
                    f"workspace snapshot blob unavailable: "
                    f"{snapshot_id}:{relative_path}"
                ) from exc
            if sha256 != captured.sha256 or size != captured.size:
                raise ValueError(
                    f"workspace snapshot blob failed validation: "
                    f"{snapshot_id}:{relative_path}"
                )

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

    def _reload_existing_snapshots(self) -> None:
        self._snapshots.clear()
        self._next_snapshot_index = 1
        self._load_existing_snapshots()

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

        files = self._load_snapshot_file_metadata(snapshot_dir, payload)
        if files is None:
            return None
        file_count = _safe_nonnegative_int(payload.get("file_count"))
        total_bytes = _safe_nonnegative_int(payload.get("total_bytes"))
        if file_count is not None and file_count != len(files):
            return None
        calculated_total_bytes = sum(item.size for item in files.values())
        if total_bytes is not None and total_bytes != calculated_total_bytes:
            return None

        skipped_payload = payload.get("skipped_files")
        skipped: dict[str, int] = {}
        if isinstance(skipped_payload, dict):
            for raw_path, raw_size in skipped_payload.items():
                size = _safe_nonnegative_int(raw_size)
                if isinstance(raw_path, str) and size is not None:
                    skipped[raw_path] = size

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
        return _SnapshotRecord(snapshot=snapshot, files=files, skipped=skipped)

    def _load_snapshot_file_metadata(
        self, snapshot_dir: Path, payload: dict[str, Any]
    ) -> dict[str, _CapturedFile] | None:
        manifest_files = payload.get("files")
        if isinstance(manifest_files, dict):
            files: dict[str, _CapturedFile] = {}
            for raw_path, raw_meta in manifest_files.items():
                if not isinstance(raw_path, str) or not isinstance(raw_meta, dict):
                    return None
                sha256 = raw_meta.get("sha256")
                size = _safe_nonnegative_int(raw_meta.get("size"))
                if not isinstance(sha256, str) or size is None:
                    return None
                try:
                    self._validate_relative_path(raw_path)
                except ValueError:
                    return None
                files[raw_path] = _CapturedFile(
                    path=raw_path, sha256=sha256, size=size
                )
            return files
        # Legacy manifest (format 1) without a files map: derive metadata by
        # streaming the persisted blobs — content is hashed chunk-wise and
        # never retained in memory.
        return self._scan_snapshot_files_metadata(snapshot_dir)

    def _scan_snapshot_files_metadata(
        self, snapshot_dir: Path
    ) -> dict[str, _CapturedFile]:
        files_dir = snapshot_dir / _SNAPSHOT_FILES_DIR_NAME
        files: dict[str, _CapturedFile] = {}
        if not files_dir.is_dir():
            return files
        self._scan_snapshot_files_directory(files_dir, (), files)
        return files

    def _scan_snapshot_files_directory(
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
                    self._scan_snapshot_files_directory(path, child_parts, files)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue

                relative_path = "/".join(child_parts)
                self._validate_relative_path(relative_path)
                sha256, size = _stream_sha256(path)
                files[relative_path] = _CapturedFile(
                    path=relative_path, sha256=sha256, size=size
                )

    def _write_manifest(self, snapshot_dir: Path, record: _SnapshotRecord) -> None:
        manifest = {
            "format": _MANIFEST_FORMAT,
            "snapshot_id": record.snapshot.snapshot_id,
            "workspace_root": str(record.snapshot.workspace_root),
            "created_at": record.snapshot.created_at,
            "label": record.snapshot.label,
            "metadata": record.snapshot.metadata,
            "file_count": record.snapshot.file_count,
            "total_bytes": record.snapshot.total_bytes,
            "files": {
                item.path: {"sha256": item.sha256, "size": item.size}
                for item in record.files.values()
            },
            "skipped_files": dict(record.skipped),
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

    def _snapshot_content_loader(self, snapshot_id: str) -> Callable[[str], bytes]:
        files_dir = self._snapshot_dir(snapshot_id) / _SNAPSHOT_FILES_DIR_NAME

        def load(relative_path: str) -> bytes:
            return self._snapshot_file_path(files_dir, relative_path).read_bytes()

        return load

    def _workspace_content_loader(self) -> Callable[[str], bytes]:
        def load(relative_path: str) -> bytes:
            return self._workspace_path(relative_path).read_bytes()

        return load

    def _capture_directory(
        self,
        directory: Path,
        relative_parts: tuple[str, ...],
        files_dir: Path,
        files: dict[str, _CapturedFile],
        skipped: dict[str, int],
    ) -> None:
        """Walk the workspace, streaming each captured file straight to disk.

        Content is copied chunk-wise into the snapshot directory while the
        sha256 is computed, so peak memory is one chunk — never the tree.
        """
        with os.scandir(directory) as entries:
            sorted_entries = sorted(entries, key=lambda entry: entry.name)

            for entry in sorted_entries:
                name = entry.name
                if name in _ALWAYS_EXCLUDED_NAMES:
                    continue
                if entry.is_symlink():
                    continue

                path = Path(entry.path)
                child_parts = (*relative_parts, name)
                if entry.is_dir(follow_symlinks=False):
                    if name in _EXCLUDED_DIRECTORY_NAMES:
                        continue
                    self._capture_directory(
                        path, child_parts, files_dir, files, skipped
                    )
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue

                relative_path = "/".join(child_parts)
                size = entry.stat(follow_symlinks=False).st_size
                if self.max_file_bytes and size > self.max_file_bytes:
                    skipped[relative_path] = size
                    continue

                destination = self._snapshot_file_path(files_dir, relative_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                copied = 0
                with path.open("rb") as source, destination.open("wb") as target:
                    while True:
                        chunk = source.read(_HASH_CHUNK_BYTES)
                        if not chunk:
                            break
                        digest.update(chunk)
                        target.write(chunk)
                        copied += len(chunk)
                files[relative_path] = _CapturedFile(
                    path=relative_path,
                    sha256=digest.hexdigest(),
                    size=copied,
                )

    def _scan_workspace(self) -> dict[str, _CapturedFile]:
        """Metadata-only scan of the workspace (streaming hash, no content)."""
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
                if name in _ALWAYS_EXCLUDED_NAMES:
                    continue
                if entry.is_symlink():
                    continue

                path = Path(entry.path)
                child_parts = (*relative_parts, name)
                if entry.is_dir(follow_symlinks=False):
                    if name in _EXCLUDED_DIRECTORY_NAMES:
                        continue
                    self._scan_directory(path, child_parts, files)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue

                relative_path = "/".join(child_parts)
                sha256, size = _stream_sha256(path)
                files[relative_path] = _CapturedFile(
                    path=relative_path,
                    sha256=sha256,
                    size=size,
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


def _stream_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _workspace_coordination(
    workspace_root: Path,
) -> tuple[RLock, dict[str, int]]:
    with _COORDINATION_LOCK:
        lock = _WORKSPACE_LOCKS.setdefault(workspace_root, RLock())
        protected = _WORKSPACE_PROTECTED_SNAPSHOTS.setdefault(workspace_root, {})
        return lock, protected


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
    *,
    before_loader: Callable[[str], bytes],
    after_loader: Callable[[str], bytes],
    max_patch_bytes: int | None = None,
) -> list[WorkspaceSnapshotDiff]:
    diffs: list[WorkspaceSnapshotDiff] = []
    for path in sorted(set(before_files) | set(after_files)):
        before = before_files.get(path)
        after = after_files.get(path)
        if before is None and after is not None:
            patch, additions, deletions = _bounded_patch(
                path,
                None,
                after,
                before_loader,
                after_loader,
                max_patch_bytes=max_patch_bytes,
            )
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
            patch, additions, deletions = _bounded_patch(
                path,
                before,
                None,
                before_loader,
                after_loader,
                max_patch_bytes=max_patch_bytes,
            )
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

        patch, additions, deletions = _bounded_patch(
            path,
            before,
            after,
            before_loader,
            after_loader,
            max_patch_bytes=max_patch_bytes,
        )
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


def _bounded_patch(
    path: str,
    before: _CapturedFile | None,
    after: _CapturedFile | None,
    before_loader: Callable[[str], bytes],
    after_loader: Callable[[str], bytes],
    *,
    max_patch_bytes: int | None,
) -> tuple[str | None, int, int]:
    if max_patch_bytes is not None and any(
        item is not None and item.size > max_patch_bytes for item in (before, after)
    ):
        return None, 0, 0
    return _unified_patch(
        path,
        before_loader(path) if before is not None else None,
        after_loader(path) if after is not None else None,
    )


def _unified_patch(
    path: str,
    before_content: bytes | None,
    after_content: bytes | None,
) -> tuple[str | None, int, int]:
    before_lines = _diff_lines(before_content if before_content is not None else b"")
    after_lines = _diff_lines(after_content if after_content is not None else b"")
    fromfile = f"a/{path}" if before_content is not None else "/dev/null"
    tofile = f"b/{path}" if after_content is not None else "/dev/null"
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
