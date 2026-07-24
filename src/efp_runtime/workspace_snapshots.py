"""Persistent workspace file snapshots for EFP runtime.

Memory model: snapshot file *content* lives only on disk (under
``.efp_runtime/workspace_snapshots/<id>/files``). The in-memory store keeps
metadata only (path, sha256, size), so holding many snapshots costs a few KB
each instead of a full copy of the workspace tree. Content is streamed from
disk one file at a time for restore/diff.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import difflib
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
from threading import RLock
import time
from typing import Any, Literal

from .types import utc_now_iso


_ALWAYS_EXCLUDED_NAMES = {
    ".efp_runtime",
    ".git",
    ".pytest_cache",
    "__pycache__",
}
# Runtime-owned state that lives inside the workspace, matched on the
# *workspace-relative path* rather than the entry name. ``.efp`` itself is real,
# agent-editable workspace content (config.json/config.jsonc, skills/, commands/,
# agents/, modes/, instructions/) and must stay capturable, diffable and
# restorable; the entries below are runtime-owned state that would otherwise
# grow every snapshot and be dropped back on top of itself by a restore.
_ALWAYS_EXCLUDED_RELATIVE_DIRS = frozenset(
    {
        # Session store / chatlogs.
        ".efp/runtime",
        # Background-task persistence store. Written live by the gateway
        # (``src/gateway/runtime_api.py`` ->
        # ``_runtime_task_persistence_storage_dir``), enabled by default
        # (``EFP_RUNTIME_TASKS_PERSISTENCE`` defaults to "true") and resolved
        # from the same ``resolve_runtime_workspace()`` root as this snapshot
        # store. Capturing it copies a live task queue into every snapshot and
        # a restore rewinds/deletes records for tasks that are still running.
        # Kept as a literal because ``efp_runtime`` must not import the
        # gateway; the resolver above is the source of truth for the name.
        ".efp/runtime_tasks",
    }
)
DEFAULT_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        # Heavy dependency/build/tool-cache directories. ``create_snapshot``
        # runs synchronously on the request path against a network PVC, once
        # per turn, and up to ``max_retained_snapshots`` copies are kept — so
        # what is captured here is a latency and disk budget, not a preference.
        # Measured on a modest tree (200 source files + 3000 build artifacts,
        # 24 MB): capturing these took 10.6 s / 24.4 MB versus 0.43 s / 0.4 MB
        # without them — 24x the time, 61x the bytes, and a Rust/Java
        # ``target/`` is GB-scale.
        #
        # Some repos do legitimately commit ``vendor/`` (Go) or build output,
        # and skipping those means a revert cannot restore them. That is
        # answered by *reporting* rather than by capturing everything: the
        # directories skipped here are recorded on the snapshot as
        # ``excluded_directories`` and surfaced through the restore/revert
        # result, so a partial restore is declared instead of hidden. Callers
        # that need a different policy pass ``excluded_directory_names``.
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
)
# Directory names that older captures may have skipped. Frozen in time on
# purpose: a snapshot written before manifests recorded ``excluded_directories``
# carries no record of what its capture skipped, so a restore cannot tell
# "this file is new" from "this file was never in scope". Deleting the latter
# is data loss, so anything under one of these names is left alone when the
# manifest has no ``excluded_directories`` key. Never narrow this set.
_HISTORIC_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
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
)
_RUNTIME_DIR_NAME = ".efp_runtime"
_SNAPSHOT_DIR_NAME = "workspace_snapshots"
_SNAPSHOT_FILES_DIR_NAME = "files"
_SNAPSHOT_MANIFEST_NAME = "manifest.json"
_SNAPSHOT_ALLOCATOR_NAME = "allocator.json"
_SNAPSHOT_ID_PREFIX = "workspace_snapshot_"
_MANIFEST_FORMAT = 2
_HASH_CHUNK_BYTES = 1024 * 1024

class WorkspaceSnapshotIncompleteError(RuntimeError):
    """A snapshot's manifest survives but its stored content does not match it.

    Raised instead of quietly treating the surviving blobs as the whole
    snapshot, which would let a restore delete everything the lost blobs
    covered.
    """


_LOGGER = logging.getLogger(__name__)

# A capture slower than this blocks the caller (today: the event loop) long
# enough to be the dominant request cost, so it is logged as a warning.
SLOW_SNAPSHOT_WARN_MS = 1000.0

# Files larger than this are not captured (recorded as skipped instead).
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
# Oldest snapshots beyond this count are pruned from disk after each capture.
DEFAULT_MAX_RETAINED_SNAPSHOTS = 20

_COORDINATION_LOCK = RLock()
_WORKSPACE_LOCKS: dict[Path, RLock] = {}
_WORKSPACE_PROTECTED_SNAPSHOTS: dict[Path, dict[str, int]] = {}
# Cap a workspace root was last reported overshooting at. Pruning runs several
# times per turn (and a fresh store is built per request), so without this the
# overshoot warning would repeat forever once tripped; it is emitted on each
# (root, cap) transition into overshoot instead.
_WORKSPACE_RETENTION_OVERSHOOT: dict[Path, int] = {}


def snapshot_storage_root(workspace_root: str | Path) -> Path:
    """Directory holding persisted snapshots for ``workspace_root``."""

    resolved = Path(workspace_root).expanduser().resolve()
    return resolved / _RUNTIME_DIR_NAME / _SNAPSHOT_DIR_NAME


@dataclass
class WorkspaceSnapshot:
    snapshot_id: str
    workspace_root: Path
    created_at: str
    label: str | None
    metadata: dict[str, Any]
    file_count: int
    total_bytes: int
    # Paths that existed at capture time but hold no captured content (above
    # ``max_file_bytes``), mapped to their observed size. A restore can neither
    # recreate nor delete these, so they are surfaced instead of hidden.
    skipped_files: dict[str, int] = field(default_factory=dict)
    # Workspace-relative directories present at capture time that the exclusion
    # policy skipped. Anything below them is outside the snapshot and therefore
    # outside a restore.
    excluded_directories: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root)
        self.metadata = dict(self.metadata)
        self.skipped_files = dict(self.skipped_files)
        self.excluded_directories = list(self.excluded_directories)


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


@dataclass(frozen=True)
class _LoadedWorkspaceFile:
    metadata: _CapturedFile
    content: bytes | None


@dataclass
class _SnapshotRecord:
    snapshot: WorkspaceSnapshot
    # Per-file metadata, or ``None`` while it is still deferred. Read it
    # through the ``files`` property, never directly.
    loaded_files: dict[str, _CapturedFile] | None = None
    # Derives ``files`` for a manifest that does not carry a file map. Deriving
    # means hashing every persisted blob, so it is called at most once per
    # record and only by a caller that genuinely needs the map.
    files_loader: Callable[[], dict[str, _CapturedFile]] | None = None
    # Paths present at capture time but not captured (e.g. above the size
    # cap), mapped to their observed size. Restore must neither recreate nor
    # delete these.
    skipped: dict[str, int] = field(default_factory=dict)
    # Workspace-relative directories the exclusion policy skipped at capture.
    excluded_directories: list[str] = field(default_factory=list)
    # Whether the manifest actually carried an ``excluded_directories`` key.
    # False for format-1 manifests and for format-2 manifests written before
    # the key existed: those record nothing about what capture skipped, so a
    # restore must not treat "present now, absent from the snapshot" as
    # "added by this turn" for anything that could have been excluded.
    excluded_directories_recorded: bool = True
    # Counts the manifest declared, kept for records whose file map is derived
    # lazily. They are the only evidence of what the snapshot is *supposed* to
    # contain, so they are checked against the blobs before the map is used.
    declared_file_count: int | None = None
    declared_total_bytes: int | None = None

    @property
    def files(self) -> dict[str, _CapturedFile]:
        """Per-file metadata, deriving it from disk on first use if needed.

        A derived map is validated against the counts the manifest declared.
        Reconciling silently down to whatever blobs survive would be data
        loss: ``restore_snapshot(delete_added=True)`` protects exactly
        ``record.files | record.skipped``, so a snapshot that lost its blobs
        would present an empty file set and delete the entire workspace. This
        is reachable in normal operation, not just on exotic disk failure --
        ``_remove_snapshot`` uses ``shutil.rmtree(ignore_errors=True)``, which
        walks bottom-up and so removes ``files/`` before ``manifest.json``;
        any partial failure leaves exactly that shape. Loading used to reject
        such a snapshot outright, and refusing to use it preserves that.
        """

        if self.loaded_files is None:
            loader = self.files_loader
            if loader is None:
                self.loaded_files = {}
            else:
                derived = loader()
                derived_bytes = sum(item.size for item in derived.values())
                if (
                    self.declared_file_count is not None
                    and self.declared_file_count != len(derived)
                ) or (
                    self.declared_total_bytes is not None
                    and self.declared_total_bytes != derived_bytes
                ):
                    raise WorkspaceSnapshotIncompleteError(
                        f"snapshot {self.snapshot.snapshot_id!r} is incomplete: "
                        f"manifest declares {self.declared_file_count} files / "
                        f"{self.declared_total_bytes} bytes but its stored "
                        f"content holds {len(derived)} files / {derived_bytes} "
                        f"bytes"
                    )
                self.loaded_files = derived
                self.snapshot.file_count = len(derived)
                self.snapshot.total_bytes = derived_bytes
        return self.loaded_files


class WorkspaceSnapshotStore:
    """Capture, diff, and restore regular workspace files with disk backing."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_retained_snapshots: int = DEFAULT_MAX_RETAINED_SNAPSHOTS,
        excluded_directory_names: Iterable[str] | None = None,
        on_snapshot_removed: Callable[[WorkspaceSnapshot], None] | None = None,
        retain_snapshots: (
            Callable[[Sequence[WorkspaceSnapshot]], Iterable[str]] | None
        ) = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.storage_root = snapshot_storage_root(self.workspace_root)
        self.max_file_bytes = max(0, int(max_file_bytes))
        self.max_retained_snapshots = max(1, int(max_retained_snapshots))
        self.excluded_directory_names = frozenset(
            DEFAULT_EXCLUDED_DIRECTORY_NAMES
            if excluded_directory_names is None
            else excluded_directory_names
        )
        self._on_snapshot_removed = on_snapshot_removed
        # Called with every retained snapshot, oldest first, and returns the
        # ids to keep past the cap. It is a *batch* hook on purpose: a
        # per-snapshot predicate cannot see the whole set, so it cannot bound
        # how much it pins, and an unbounded pin grows the store forever.
        self._retain_snapshots = retain_snapshots
        self._snapshots: dict[str, _SnapshotRecord] = {}
        # snapshot_id -> (manifest mtime, derived file metadata) for manifests
        # that carry no file map. Every runtime operation reloads every
        # manifest and records are rebuilt each time, so without this a
        # snapshot used twice would be re-hashed twice.
        self._legacy_files_cache: dict[
            str, tuple[int | None, dict[str, _CapturedFile]]
        ] = {}
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
        *,
        protect: bool = False,
    ) -> WorkspaceSnapshot:
        with self._lock:
            started = time.perf_counter()
            self._reload_existing_snapshots()
            reload_ms = (time.perf_counter() - started) * 1000.0
            snapshot_id = self._allocate_snapshot_id()
            snapshot_dir = self._snapshot_dir(snapshot_id)
            if snapshot_dir.exists():
                shutil.rmtree(snapshot_dir)
            files_dir = snapshot_dir / _SNAPSHOT_FILES_DIR_NAME
            files_dir.mkdir(parents=True, exist_ok=False)

            files: dict[str, _CapturedFile] = {}
            skipped: dict[str, int] = {}
            excluded_directories: list[str] = []
            try:
                capture_started = time.perf_counter()
                self._capture_directory(
                    self.workspace_root,
                    (),
                    files_dir,
                    files,
                    skipped,
                    excluded_directories,
                )
                capture_ms = (time.perf_counter() - capture_started) * 1000.0
                excluded_directories.sort()
                snapshot = WorkspaceSnapshot(
                    snapshot_id=snapshot_id,
                    workspace_root=self.workspace_root,
                    created_at=utc_now_iso(),
                    label=label,
                    metadata=dict(metadata or {}),
                    file_count=len(files),
                    total_bytes=sum(item.size for item in files.values()),
                    skipped_files=dict(skipped),
                    excluded_directories=list(excluded_directories),
                )
                record = _SnapshotRecord(
                    snapshot=deepcopy(snapshot),
                    loaded_files=files,
                    skipped=skipped,
                    excluded_directories=excluded_directories,
                )
                # Manifest is written last: its presence marks the snapshot
                # as complete (partial dirs without a manifest are ignored
                # by _load_existing_snapshots).
                self._write_manifest(snapshot_dir, record)
            except BaseException:
                shutil.rmtree(snapshot_dir, ignore_errors=True)
                raise

            self._snapshots[snapshot.snapshot_id] = record
            if protect:
                self._protected_snapshot_ids[snapshot.snapshot_id] = (
                    self._protected_snapshot_ids.get(snapshot.snapshot_id, 0) + 1
                )
            prune_started = time.perf_counter()
            self._prune_old_snapshots(protect={snapshot.snapshot_id})
            prune_ms = (time.perf_counter() - prune_started) * 1000.0
            total_ms = (time.perf_counter() - started) * 1000.0
            _LOGGER.info(
                "workspace_snapshot.created id=%s root=%s files=%d skipped=%d "
                "excluded_dirs=%d bytes=%d reload_ms=%.0f capture_ms=%.0f "
                "prune_ms=%.0f total_ms=%.0f",
                snapshot.snapshot_id,
                self.workspace_root,
                snapshot.file_count,
                len(skipped),
                len(excluded_directories),
                snapshot.total_bytes,
                reload_ms,
                capture_ms,
                prune_ms,
                total_ms,
            )
            if total_ms > SLOW_SNAPSHOT_WARN_MS:
                _LOGGER.warning(
                    "workspace_snapshot.slow id=%s root=%s files=%d bytes=%d "
                    "reload_ms=%.0f capture_ms=%.0f prune_ms=%.0f total_ms=%.0f "
                    "threshold_ms=%.0f",
                    snapshot.snapshot_id,
                    self.workspace_root,
                    snapshot.file_count,
                    snapshot.total_bytes,
                    reload_ms,
                    capture_ms,
                    prune_ms,
                    total_ms,
                    SLOW_SNAPSHOT_WARN_MS,
                )
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
            self.release_snapshot_protection(snapshot_id)

    def release_snapshot_protection(self, snapshot_id: str) -> None:
        """Release one retained-snapshot lease and reapply the retention cap."""

        with self._lock:
            remaining = self._protected_snapshot_ids.get(snapshot_id, 0) - 1
            if remaining < 0:
                raise ValueError(f"snapshot is not protected: {snapshot_id}")
            if remaining:
                self._protected_snapshot_ids[snapshot_id] = remaining
            else:
                self._protected_snapshot_ids.pop(snapshot_id, None)
            self._reload_existing_snapshots()
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
                after_loader=self._workspace_file_loader(),
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
                # Snapshots taken before a directory became runtime-owned still
                # hold its blobs; writing them back drops a stale session store
                # / background-task queue on top of the live one.
                if _directory_prefixes(relative_path) & _ALWAYS_EXCLUDED_RELATIVE_DIRS:
                    continue
                self._write_file(relative_path, load_content(relative_path))

            if delete_added:
                current_files = self._scan_workspace()
                protected = set(record.files) | set(record.skipped)
                for relative_path in sorted(set(current_files) - protected):
                    # "Absent from the snapshot" only means "added by the turn"
                    # for paths the capture actually looked at. Anything the
                    # capture excluded was never in scope, and deleting it is
                    # data loss, not a revert.
                    if _is_outside_snapshot_scope(record, relative_path):
                        continue
                    self._delete_regular_file(relative_path)

            return deepcopy(record.snapshot)

    def delete_snapshot(self, snapshot_id: str) -> bool:
        with self._lock:
            self._reload_existing_snapshots()
            self._require_snapshot(snapshot_id)
            if self._protected_snapshot_ids.get(snapshot_id, 0) > 0:
                raise RuntimeError(f"workspace snapshot is protected: {snapshot_id}")
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
            self._write_next_snapshot_index()
            return snapshot_id

    def _prune_old_snapshots(self, *, protect: set[str]) -> None:
        if len(self._snapshots) <= self.max_retained_snapshots:
            # Nothing can be retained past a cap nothing reaches; clear any
            # standing overshoot so a later one is reported again.
            self._report_retention_overshoot(len(self._snapshots))
            return
        protected = protect | set(self._protected_snapshot_ids)
        ordered = sorted(
            self._snapshots,
            key=lambda snapshot_id: _snapshot_sort_key(snapshot_id),
        )
        if self._retain_snapshots is not None:
            try:
                requested = set(
                    self._retain_snapshots(
                        [
                            deepcopy(self._snapshots[snapshot_id].snapshot)
                            for snapshot_id in ordered
                        ]
                    )
                )
            except Exception:
                _LOGGER.exception(
                    "failed to inspect retention for workspace snapshots under %s",
                    self.workspace_root,
                )
                requested = set()
            protected |= requested & set(self._snapshots)
        retained = protected & set(self._snapshots)
        self._report_retention_overshoot(len(retained))
        excess = len(self._snapshots) - self.max_retained_snapshots
        for snapshot_id in ordered:
            if excess <= 0:
                break
            if snapshot_id in protected:
                continue
            self._remove_snapshot(snapshot_id)
            excess -= 1

    def _report_retention_overshoot(self, retained: int) -> None:
        """Warn once per (root, cap) transition into retention overshoot.

        Retention wins over the cap (dropping a referenced snapshot turns a
        later revert into a silent history-only trim), so the overshoot is
        reported instead of being silently unbounded. Pruning runs several
        times per turn, so the report is edge-triggered rather than repeated.
        """
        cap = self.max_retained_snapshots
        with _COORDINATION_LOCK:
            if retained <= cap:
                _WORKSPACE_RETENTION_OVERSHOOT.pop(self.workspace_root, None)
                return
            if _WORKSPACE_RETENTION_OVERSHOOT.get(self.workspace_root) == cap:
                return
            _WORKSPACE_RETENTION_OVERSHOOT[self.workspace_root] = cap
        _LOGGER.warning(
            "workspace_snapshot.retention_exceeds_cap root=%s retained=%d "
            "cap=%d total=%d",
            self.workspace_root,
            retained,
            cap,
            len(self._snapshots),
        )

    def _remove_snapshot(self, snapshot_id: str) -> None:
        try:
            if self._on_snapshot_removed is not None:
                self._on_snapshot_removed(
                    deepcopy(self._snapshots[snapshot_id].snapshot)
                )
        except Exception:
            _LOGGER.exception(
                "failed to invalidate references for workspace snapshot %s",
                snapshot_id,
            )
        finally:
            shutil.rmtree(self._snapshot_dir(snapshot_id), ignore_errors=True)
            del self._snapshots[snapshot_id]
            self._legacy_files_cache.pop(snapshot_id, None)

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

        self._next_snapshot_index = max(
            self._next_snapshot_index,
            highest_index + 1,
            self._read_next_snapshot_index(),
        )

    def _reload_existing_snapshots(self) -> None:
        self._snapshots.clear()
        self._load_existing_snapshots()

    def _read_next_snapshot_index(self) -> int:
        allocator_path = self.storage_root / _SNAPSHOT_ALLOCATOR_NAME
        try:
            payload = json.loads(allocator_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 1
        if not isinstance(payload, dict):
            return 1
        next_index = _safe_nonnegative_int(payload.get("next_snapshot_index"))
        return max(1, next_index or 1)

    def _write_next_snapshot_index(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        allocator_path = self.storage_root / _SNAPSHOT_ALLOCATOR_NAME
        temporary_path = allocator_path.with_name(
            f".{_SNAPSHOT_ALLOCATOR_NAME}.{os.getpid()}.{id(self)}.tmp"
        )
        try:
            temporary_path.write_text(
                json.dumps(
                    {"next_snapshot_index": self._next_snapshot_index},
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, allocator_path)
        finally:
            temporary_path.unlink(missing_ok=True)

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

        loaded_files, files_loader = self._resolve_snapshot_file_metadata(
            snapshot_dir, payload
        )
        if loaded_files is None and files_loader is None:
            return None
        declared_file_count = _safe_nonnegative_int(payload.get("file_count"))
        declared_total_bytes = _safe_nonnegative_int(payload.get("total_bytes"))
        if loaded_files is not None:
            file_count = len(loaded_files)
            calculated_total_bytes = sum(item.size for item in loaded_files.values())
            if declared_file_count is not None and declared_file_count != file_count:
                return None
            if (
                declared_total_bytes is not None
                and declared_total_bytes != calculated_total_bytes
            ):
                return None
        else:
            # Header-only load: the manifest's own counts describe the snapshot
            # until a caller needs the file map. They are reconciled against
            # the blobs at that point (see ``_SnapshotRecord.files``).
            file_count = declared_file_count if declared_file_count is not None else 0
            calculated_total_bytes = (
                declared_total_bytes if declared_total_bytes is not None else 0
            )

        skipped_payload = payload.get("skipped_files")
        skipped: dict[str, int] = {}
        if isinstance(skipped_payload, dict):
            for raw_path, raw_size in skipped_payload.items():
                size = _safe_nonnegative_int(raw_size)
                if isinstance(raw_path, str) and size is not None:
                    skipped[raw_path] = size

        excluded_payload = payload.get("excluded_directories")
        excluded_directories_recorded = isinstance(excluded_payload, list)
        excluded_directories = sorted(
            {item for item in excluded_payload if isinstance(item, str)}
            if excluded_directories_recorded
            else set()
        )

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
            file_count=file_count,
            total_bytes=calculated_total_bytes,
            skipped_files=dict(skipped),
            excluded_directories=list(excluded_directories),
        )
        return _SnapshotRecord(
            snapshot=snapshot,
            loaded_files=loaded_files,
            files_loader=files_loader,
            skipped=skipped,
            excluded_directories=excluded_directories,
            excluded_directories_recorded=excluded_directories_recorded,
            declared_file_count=declared_file_count,
            declared_total_bytes=declared_total_bytes,
        )

    def _resolve_snapshot_file_metadata(
        self, snapshot_dir: Path, payload: dict[str, Any]
    ) -> tuple[
        dict[str, _CapturedFile] | None,
        Callable[[], dict[str, _CapturedFile]] | None,
    ]:
        """Per-file metadata for a manifest, deferred when it has to be derived.

        Returns ``(files, None)`` when the metadata is known without touching
        blobs, ``(None, loader)`` when it has to be derived on demand, and
        ``(None, None)`` when the manifest is invalid.

        A legacy manifest (format 1) carries no file map, so the metadata can
        only come from hashing every persisted blob. That runs per store
        construction and stores are built per request: one production pod was
        re-hashing 577 MB across 27 retained snapshots — 26 s a turn — for a
        feature that was switched off. Listing, pruning, retention and
        protection all work from the manifest header alone, so the hashing is
        deferred to the first caller that actually needs the file map
        (diff/restore).
        """
        manifest_files = payload.get("files")
        if isinstance(manifest_files, dict):
            return self._parse_manifest_file_metadata(manifest_files), None
        if (
            _safe_nonnegative_int(payload.get("file_count")) is None
            or _safe_nonnegative_int(payload.get("total_bytes")) is None
        ):
            # No file map and no header counts: nothing describes the snapshot
            # without reading the blobs, so there is nothing to defer.
            return self._rehash_legacy_snapshot_files(snapshot_dir, payload), None
        return None, lambda: self._rehash_legacy_snapshot_files(snapshot_dir, payload)

    def _parse_manifest_file_metadata(
        self, manifest_files: dict[Any, Any]
    ) -> dict[str, _CapturedFile] | None:
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
            files[raw_path] = _CapturedFile(path=raw_path, sha256=sha256, size=size)
        return files

    def _rehash_legacy_snapshot_files(
        self, snapshot_dir: Path, payload: dict[str, Any]
    ) -> dict[str, _CapturedFile]:
        """Derive one legacy snapshot's file metadata by streaming its blobs.

        Content is hashed chunk-wise and never retained in memory. The result
        is cached per (snapshot, manifest mtime) because every runtime
        operation rebuilds every record, and it is logged loudly whenever the
        hashing genuinely runs: on a network volume it can cost seconds per
        snapshot, and that log line is what makes the cost diagnosable.
        """
        snapshot_id = snapshot_dir.name
        manifest_mtime = _manifest_mtime_ns(snapshot_dir)
        cached = self._legacy_files_cache.get(snapshot_id)
        if cached is not None and cached[0] == manifest_mtime:
            return dict(cached[1])

        started = time.perf_counter()
        legacy_files = self._scan_snapshot_files_metadata(snapshot_dir)
        total_bytes = sum(item.size for item in legacy_files.values())
        _LOGGER.warning(
            "workspace_snapshot.legacy_manifest_rehash id=%s format=%s "
            "files=%d bytes=%d elapsed_ms=%.0f",
            snapshot_id,
            payload.get("format"),
            len(legacy_files),
            total_bytes,
            (time.perf_counter() - started) * 1000.0,
        )
        declared_file_count = _safe_nonnegative_int(payload.get("file_count"))
        declared_total_bytes = _safe_nonnegative_int(payload.get("total_bytes"))
        counts_differ = (
            declared_file_count is not None
            and declared_file_count != len(legacy_files)
        ) or (
            declared_total_bytes is not None and declared_total_bytes != total_bytes
        )
        if counts_differ:
            # Never retain a map that the manifest says is incomplete. The
            # blobs may be restored without rewriting the manifest, in which
            # case the next attempt must scan them again rather than reuse the
            # failed result.
            self._legacy_files_cache.pop(snapshot_id, None)
            # The header promised a snapshot the blobs no longer back. What is
            # on disk is the truth a restore can deliver, so it is used — and
            # reported, because it means the snapshot is incomplete.
            _LOGGER.warning(
                "workspace_snapshot.legacy_manifest_counts_differ id=%s "
                "manifest_files=%s manifest_bytes=%s files=%d bytes=%d",
                snapshot_id,
                declared_file_count,
                declared_total_bytes,
                len(legacy_files),
                total_bytes,
            )
        else:
            self._legacy_files_cache[snapshot_id] = (
                manifest_mtime,
                legacy_files,
            )
        return dict(legacy_files)

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
            "excluded_directories": sorted(record.excluded_directories),
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

    def _workspace_file_loader(
        self,
    ) -> Callable[[str], _LoadedWorkspaceFile | None]:
        def load(relative_path: str) -> _LoadedWorkspaceFile | None:
            path = self._workspace_path(relative_path)
            digest = hashlib.sha256()
            size = 0
            chunks: list[bytes] | None = []
            try:
                with path.open("rb") as handle:
                    while True:
                        chunk = handle.read(_HASH_CHUNK_BYTES)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
                        if chunks is None:
                            continue
                        if self.max_file_bytes and size > self.max_file_bytes:
                            chunks = None
                        else:
                            chunks.append(chunk)
            except (FileNotFoundError, IsADirectoryError):
                return None
            return _LoadedWorkspaceFile(
                metadata=_CapturedFile(
                    path=relative_path,
                    sha256=digest.hexdigest(),
                    size=size,
                ),
                content=b"".join(chunks) if chunks is not None else None,
            )

        return load

    def _capture_directory(
        self,
        directory: Path,
        relative_parts: tuple[str, ...],
        files_dir: Path,
        files: dict[str, _CapturedFile],
        skipped: dict[str, int],
        excluded_directories: list[str],
    ) -> None:
        """Walk the workspace, streaming each captured file straight to disk.

        Content is copied chunk-wise into the snapshot directory while the
        sha256 is computed, so peak memory is one chunk — never the tree.

        The mirrored destination directory is created at most once per source
        directory (lazily, on the first file actually captured here) instead of
        once per file: on a network volume the redundant ``mkdir`` was a large
        share of all round-trips, and staying lazy keeps directories whose
        files are all excluded/skipped from leaving stray empty mirrors.
        """
        destination_dir_ready = False

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
                    relative_dir = "/".join(child_parts)
                    if relative_dir in _ALWAYS_EXCLUDED_RELATIVE_DIRS:
                        continue
                    if name in self.excluded_directory_names:
                        # Reported on the snapshot: everything below is outside
                        # the snapshot and therefore outside a later restore.
                        excluded_directories.append(relative_dir)
                        continue
                    self._capture_directory(
                        path,
                        child_parts,
                        files_dir,
                        files,
                        skipped,
                        excluded_directories,
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
                if not destination_dir_ready:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination_dir_ready = True
                digest = hashlib.sha256()
                copied = 0
                exceeded_limit = False
                with path.open("rb") as source, destination.open("wb") as target:
                    while True:
                        chunk = source.read(_HASH_CHUNK_BYTES)
                        if not chunk:
                            break
                        if (
                            self.max_file_bytes
                            and copied + len(chunk) > self.max_file_bytes
                        ):
                            skipped[relative_path] = copied + len(chunk)
                            exceeded_limit = True
                            break
                        digest.update(chunk)
                        target.write(chunk)
                        copied += len(chunk)
                if exceeded_limit:
                    destination.unlink(missing_ok=True)
                    continue
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
                    relative_dir = "/".join(child_parts)
                    if relative_dir in _ALWAYS_EXCLUDED_RELATIVE_DIRS:
                        continue
                    if name in self.excluded_directory_names:
                        continue
                    self._scan_directory(path, child_parts, files)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue

                relative_path = "/".join(child_parts)
                try:
                    sha256, size = _stream_sha256(path)
                except (FileNotFoundError, IsADirectoryError):
                    continue
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


def _directory_prefixes(relative_path: str) -> set[str]:
    """Every workspace-relative directory ``relative_path`` sits under."""

    directories = relative_path.split("/")[:-1]
    return {"/".join(directories[: index + 1]) for index in range(len(directories))}


def _is_outside_snapshot_scope(record: _SnapshotRecord, relative_path: str) -> bool:
    """Whether ``relative_path`` sits under a directory the capture skipped."""

    directories = relative_path.split("/")[:-1]
    if not directories:
        return False
    prefixes = {
        "/".join(directories[: index + 1]) for index in range(len(directories))
    }
    if prefixes & set(record.excluded_directories):
        return True
    if prefixes & _ALWAYS_EXCLUDED_RELATIVE_DIRS:
        return True
    if record.excluded_directories_recorded:
        return False
    # No record of what this snapshot excluded: assume the historic defaults
    # were in force rather than deleting files that may never have been in it.
    return any(name in _HISTORIC_EXCLUDED_DIRECTORY_NAMES for name in directories)


def _manifest_mtime_ns(snapshot_dir: Path) -> int | None:
    try:
        return (snapshot_dir / _SNAPSHOT_MANIFEST_NAME).stat().st_mtime_ns
    except OSError:
        return None


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
    after_loader: Callable[[str], _LoadedWorkspaceFile | None],
    max_patch_bytes: int | None = None,
) -> list[WorkspaceSnapshotDiff]:
    diffs: list[WorkspaceSnapshotDiff] = []
    for path in sorted(set(before_files) | set(after_files)):
        before = before_files.get(path)
        scanned_after = after_files.get(path)
        if (
            before is not None
            and scanned_after is not None
            and before.sha256 == scanned_after.sha256
        ):
            continue
        loaded_after = after_loader(path)
        after = loaded_after.metadata if loaded_after is not None else None
        after_content = loaded_after.content if loaded_after is not None else None
        if before is None and after is not None:
            patch, additions, deletions = _bounded_patch(
                path,
                None,
                after,
                before_loader,
                after_content,
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
                None,
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
            after_content,
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
    after_content: bytes | None,
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
        after_content if after is not None else None,
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
