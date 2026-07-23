"""Memory-model tests for WorkspaceSnapshotStore.

The store must keep only metadata in RAM: content is streamed to/from the
disk-backed snapshot directory. These tests pin the behaviors introduced by
the memory fix: manifest-based (blob-free) reload, per-file size cap with
restore protection, heavy-directory exclusion, retention pruning, and
legacy (format-1) manifest compatibility.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest

from src.efp_runtime.workspace_snapshots import (
    WorkspaceSnapshot,
    WorkspaceSnapshotStore,
)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def _snapshot_dir(root: Path, snapshot_id: str) -> Path:
    return root / ".efp_runtime" / "workspace_snapshots" / snapshot_id


def test_manifest_contains_file_metadata_and_reload_does_not_read_blobs(
    tmp_path: Path,
):
    _write_text(tmp_path / "a.txt", "alpha\n")
    _write_text(tmp_path / "sub/b.txt", "beta\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    manifest_path = _snapshot_dir(tmp_path, snapshot.snapshot_id) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["format"] == 2
    assert set(manifest["files"]) == {"a.txt", "sub/b.txt"}
    assert all("sha256" in meta and "size" in meta for meta in manifest["files"].values())

    # Deleting a persisted blob must not prevent metadata reload: a v2
    # manifest is the source of truth and blobs are not touched at load time.
    blob = _snapshot_dir(tmp_path, snapshot.snapshot_id) / "files" / "a.txt"
    blob.unlink()
    reloaded = WorkspaceSnapshotStore(tmp_path)
    assert [item.snapshot_id for item in reloaded.list_snapshots()] == [
        snapshot.snapshot_id
    ]


@pytest.mark.parametrize("corruption", ["missing", "truncated"])
def test_restore_validates_all_blobs_before_mutating_workspace(
    tmp_path: Path,
    corruption: str,
):
    _write_text(tmp_path / "a.txt", "captured a\n")
    _write_text(tmp_path / "b.txt", "captured b\n")
    snapshot = WorkspaceSnapshotStore(tmp_path).create_snapshot()
    _write_text(tmp_path / "a.txt", "current a\n")
    _write_text(tmp_path / "b.txt", "current b\n")

    blob = _snapshot_dir(tmp_path, snapshot.snapshot_id) / "files" / "b.txt"
    if corruption == "missing":
        blob.unlink()
        expected_error = OSError
    else:
        blob.write_bytes(b"bad")
        expected_error = ValueError

    reloaded = WorkspaceSnapshotStore(tmp_path)
    with pytest.raises(expected_error, match="b.txt"):
        reloaded.diff_snapshot(snapshot.snapshot_id)
    with pytest.raises(expected_error, match="b.txt"):
        reloaded.restore_snapshot(snapshot.snapshot_id)

    assert (tmp_path / "a.txt").read_bytes() == b"current a\n"
    assert (tmp_path / "b.txt").read_bytes() == b"current b\n"


def test_size_capped_files_are_skipped_and_protected_on_restore(tmp_path: Path):
    _write_text(tmp_path / "small.txt", "small\n")
    _write_text(tmp_path / "big.bin", "x" * 500)
    store = WorkspaceSnapshotStore(tmp_path, max_file_bytes=100)
    snapshot = store.create_snapshot()
    assert snapshot.file_count == 1  # big.bin skipped

    manifest_path = _snapshot_dir(tmp_path, snapshot.snapshot_id) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["skipped_files"] == {"big.bin": 500}

    # Mutate both files after the snapshot.
    _write_text(tmp_path / "small.txt", "small changed\n")
    _write_text(tmp_path / "big.bin", "y" * 600)

    # The skipped file never shows up in diffs (no captured content).
    diff_paths = {item.path for item in store.diff_snapshot(snapshot.snapshot_id)}
    assert diff_paths == {"small.txt"}

    store.restore_snapshot(snapshot.snapshot_id, delete_added=True)
    # Captured file reverted; skipped file neither reverted nor deleted.
    assert (tmp_path / "small.txt").read_bytes() == b"small\n"
    assert (tmp_path / "big.bin").read_bytes() == b"y" * 600


def test_file_size_cap_stops_file_that_grows_during_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source_path = tmp_path / "growing.bin"
    source_path.write_bytes(b"1234")
    original_open = Path.open
    reader_used = False

    class GrowingReader:
        def __init__(self):
            self.handle = None
            self.grown = False

        def __enter__(self):
            self.handle = original_open(source_path, "rb")
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            assert self.handle is not None
            self.handle.close()

        def read(self, size=-1):
            assert self.handle is not None
            chunk = self.handle.read(size)
            if chunk and not self.grown:
                self.grown = True
                with original_open(source_path, "ab") as writer:
                    writer.write(b"5678")
            return chunk

    def growing_open(path, mode="r", *args, **kwargs):
        nonlocal reader_used
        if path == source_path and mode == "rb" and not reader_used:
            reader_used = True
            return GrowingReader()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing_open)
    store = WorkspaceSnapshotStore(tmp_path, max_file_bytes=4)

    snapshot = store.create_snapshot()

    assert snapshot.file_count == 0
    assert snapshot.total_bytes == 0
    source_path.write_bytes(b"current")
    store.restore_snapshot(snapshot.snapshot_id, delete_added=True)
    assert source_path.read_bytes() == b"current"


def test_oversized_current_files_have_metadata_only_diffs(tmp_path: Path):
    _write_text(tmp_path / "modified.bin", "small")
    store = WorkspaceSnapshotStore(tmp_path, max_file_bytes=10)
    snapshot = store.create_snapshot()
    _write_text(tmp_path / "modified.bin", "m" * 20)
    _write_text(tmp_path / "added.bin", "a" * 20)

    diffs = {item.path: item for item in store.diff_snapshot(snapshot.snapshot_id)}

    assert diffs["modified.bin"].status == "modified"
    assert diffs["modified.bin"].after_bytes == 20
    assert diffs["modified.bin"].patch is None
    assert diffs["modified.bin"].additions == 0
    assert diffs["modified.bin"].deletions == 0
    assert diffs["added.bin"].status == "added"
    assert diffs["added.bin"].after_bytes == 20
    assert diffs["added.bin"].patch is None


def test_heavy_dependency_directories_are_excluded(tmp_path: Path):
    _write_text(tmp_path / "src/main.py", "print('hi')\n")
    _write_text(tmp_path / "build", "#!/bin/sh\necho build\n")
    _write_text(tmp_path / "node_modules/pkg/index.js", "module.exports = 1\n")
    _write_text(tmp_path / ".venv/lib/site.py", "# venv\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert snapshot.file_count == 2

    _write_text(tmp_path / "build", "changed\n")
    store.restore_snapshot(snapshot.snapshot_id, delete_added=True)
    # Excluded directories are invisible to restore: never deleted as "added".
    assert (tmp_path / "build").read_bytes() == b"#!/bin/sh\necho build\n"
    assert (tmp_path / "node_modules/pkg/index.js").is_file()
    assert (tmp_path / ".venv/lib/site.py").is_file()


def test_retention_cap_prunes_oldest_snapshots(tmp_path: Path):
    _write_text(tmp_path / "a.txt", "v1\n")
    store = WorkspaceSnapshotStore(tmp_path, max_retained_snapshots=2)
    first = store.create_snapshot(label="first")
    second = store.create_snapshot(label="second")
    third = store.create_snapshot(label="third")

    listed = [item.snapshot_id for item in store.list_snapshots()]
    assert listed == [second.snapshot_id, third.snapshot_id]
    assert not _snapshot_dir(tmp_path, first.snapshot_id).exists()
    assert _snapshot_dir(tmp_path, third.snapshot_id).exists()

    # A reloaded store sees the same pruned set.
    reloaded = WorkspaceSnapshotStore(tmp_path, max_retained_snapshots=2)
    assert [item.snapshot_id for item in reloaded.list_snapshots()] == listed


def test_retention_removes_snapshot_when_invalidation_callback_fails(tmp_path: Path):
    def invalidate(snapshot):
        if snapshot.label == "first":
            raise OSError("session metadata unavailable")

    store = WorkspaceSnapshotStore(
        tmp_path,
        max_retained_snapshots=1,
        on_snapshot_removed=invalidate,
    )
    first = store.create_snapshot(label="first")

    second = store.create_snapshot(label="second")

    retained = store.list_snapshots()
    assert [snapshot.snapshot_id for snapshot in retained] == [second.snapshot_id]
    assert not _snapshot_dir(tmp_path, first.snapshot_id).exists()

    third = store.create_snapshot(label="third")
    assert [snapshot.snapshot_id for snapshot in store.list_snapshots()] == [
        third.snapshot_id
    ]


def test_retention_ignores_snapshot_retainer_failures(tmp_path: Path):
    def retain(snapshots):
        raise OSError("session summary unavailable")

    store = WorkspaceSnapshotStore(
        tmp_path,
        max_retained_snapshots=1,
        retain_snapshots=retain,
    )
    first = store.create_snapshot(label="first")
    second = store.create_snapshot(label="second", protect=True)

    assert [snapshot.snapshot_id for snapshot in store.list_snapshots()] == [
        second.snapshot_id
    ]
    assert not _snapshot_dir(tmp_path, first.snapshot_id).exists()
    store.release_snapshot_protection(second.snapshot_id)


def test_snapshot_protection_is_shared_across_store_instances(tmp_path: Path):
    _write_text(tmp_path / "state.txt", "first\n")
    first_store = WorkspaceSnapshotStore(tmp_path, max_retained_snapshots=2)
    first = first_store.create_snapshot(label="first")
    _write_text(tmp_path / "state.txt", "second\n")
    first_store.create_snapshot(label="second")
    second_store = WorkspaceSnapshotStore(tmp_path, max_retained_snapshots=2)

    with first_store.protect_snapshot(first.snapshot_id):
        _write_text(tmp_path / "state.txt", "third\n")
        third = second_store.create_snapshot(label="third")
        first_store.restore_snapshot(first.snapshot_id)

    assert (tmp_path / "state.txt").read_bytes() == b"first\n"
    assert {
        snapshot.snapshot_id for snapshot in first_store.list_snapshots()
    } == {first.snapshot_id, third.snapshot_id}


def test_protected_snapshot_cannot_be_deleted_by_another_store(tmp_path: Path):
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot(label="leased")
    competing_store = WorkspaceSnapshotStore(tmp_path)

    with store.protect_snapshot(snapshot.snapshot_id):
        with pytest.raises(RuntimeError, match="workspace snapshot is protected"):
            competing_store.delete_snapshot(snapshot.snapshot_id)
        assert competing_store.restore_snapshot(snapshot.snapshot_id).snapshot_id == (
            snapshot.snapshot_id
        )

    assert competing_store.delete_snapshot(snapshot.snapshot_id) is True


def test_snapshot_ids_are_not_reused_after_delete_and_reload(tmp_path: Path):
    store = WorkspaceSnapshotStore(tmp_path)
    first = store.create_snapshot(label="first")
    store.delete_snapshot(first.snapshot_id)

    second = store.create_snapshot(label="second")
    store.delete_snapshot(second.snapshot_id)
    restarted = WorkspaceSnapshotStore(tmp_path)
    third = restarted.create_snapshot(label="third")

    assert len({first.snapshot_id, second.snapshot_id, third.snapshot_id}) == 3


def test_diff_uses_one_current_file_read_for_metadata_and_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "state.txt"
    _write_text(path, "before\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()
    _write_text(path, "intermediate\n")
    original_scan = store._scan_workspace

    def scan_then_change():
        files = original_scan()
        _write_text(path, "final\n")
        return files

    monkeypatch.setattr(store, "_scan_workspace", scan_then_change)

    [diff] = store.diff_snapshot(snapshot.snapshot_id)

    final_content = b"final\n"
    assert diff.after_hash == hashlib.sha256(final_content).hexdigest()
    assert diff.after_bytes == len(final_content)
    assert diff.patch is not None
    assert "+final" in diff.patch
    assert "intermediate" not in diff.patch


def test_diff_treats_file_deleted_after_scan_as_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "state.txt"
    _write_text(path, "before\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()
    _write_text(path, "intermediate\n")
    original_scan = store._scan_workspace

    def scan_then_delete():
        files = original_scan()
        path.unlink()
        return files

    monkeypatch.setattr(store, "_scan_workspace", scan_then_delete)

    [diff] = store.diff_snapshot(snapshot.snapshot_id)

    assert diff.status == "deleted"
    assert diff.after_hash is None
    assert diff.after_bytes is None


def _write_legacy_snapshot(root: Path, snapshot_id: str, files: dict[str, str]) -> None:
    """A snapshot as the pre-file-map code wrote it: no ``files`` map."""
    snapshot_dir = _snapshot_dir(root, snapshot_id)
    for relative_path, content in files.items():
        _write_text(snapshot_dir / "files" / relative_path, content)
    manifest = {
        "snapshot_id": snapshot_id,
        "workspace_root": str(root),
        "created_at": "2026-01-01T00:00:00+00:00",
        "label": snapshot_id,
        "metadata": {},
        "file_count": len(files),
        "total_bytes": sum(len(content.encode("utf-8")) for content in files.values()),
    }
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class _BlobHashWatch:
    """Records which snapshot blobs get stream-hashed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
        from src.efp_runtime import workspace_snapshots

        self._storage_root = (root / ".efp_runtime" / "workspace_snapshots").resolve()
        self.hashed: list[Path] = []
        original = workspace_snapshots._stream_sha256

        def tracked(path: Path):
            resolved = Path(path).resolve()
            if self._storage_root in resolved.parents:
                self.hashed.append(resolved)
            return original(path)

        monkeypatch.setattr(workspace_snapshots, "_stream_sha256", tracked)

    def snapshots_hashed(self) -> set[str]:
        return {
            path.relative_to(self._storage_root).parts[0] for path in self.hashed
        }


def _rehash_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("workspace_snapshot.legacy_manifest_rehash")
    ]


def test_loading_legacy_snapshots_reads_no_blobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
):
    """Construction must not re-hash what the manifests already describe.

    A production pod re-hashed 577 MB across 27 retained legacy snapshots on
    every store construction — and a store is built per chat request.
    """
    for index in range(1, 4):
        _write_legacy_snapshot(
            tmp_path,
            f"workspace_snapshot_{index}",
            {"keep.txt": f"captured {index}\n", "sub/other.txt": "other\n"},
        )
    watch = _BlobHashWatch(monkeypatch, tmp_path)
    caplog.set_level(logging.WARNING)

    store = WorkspaceSnapshotStore(tmp_path)
    listed = store.list_snapshots()

    assert [item.snapshot_id for item in listed] == [
        "workspace_snapshot_1",
        "workspace_snapshot_2",
        "workspace_snapshot_3",
    ]
    # The header describes the snapshot well enough to list it.
    assert [item.file_count for item in listed] == [2, 2, 2]
    assert [item.total_bytes for item in listed] == [
        len(b"captured 1\n") + len(b"other\n"),
        len(b"captured 2\n") + len(b"other\n"),
        len(b"captured 3\n") + len(b"other\n"),
    ]
    assert watch.hashed == []
    assert _rehash_warnings(caplog) == []


def test_pruning_legacy_snapshots_reads_no_blobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
):
    """Retention runs several times a turn and must stay blob-free."""
    for index in range(1, 4):
        _write_legacy_snapshot(
            tmp_path, f"workspace_snapshot_{index}", {"keep.txt": f"captured {index}\n"}
        )
    _write_text(tmp_path / "live.txt", "live\n")
    watch = _BlobHashWatch(monkeypatch, tmp_path)
    caplog.set_level(logging.WARNING)

    store = WorkspaceSnapshotStore(tmp_path, max_retained_snapshots=2)
    created = store.create_snapshot(label="fresh")

    assert {item.snapshot_id for item in store.list_snapshots()} == {
        "workspace_snapshot_3",
        created.snapshot_id,
    }
    assert not _snapshot_dir(tmp_path, "workspace_snapshot_1").exists()
    assert watch.hashed == []
    assert _rehash_warnings(caplog) == []


def test_diffing_one_legacy_snapshot_rehashes_only_that_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
):
    for index in range(1, 4):
        _write_legacy_snapshot(
            tmp_path, f"workspace_snapshot_{index}", {"keep.txt": f"captured {index}\n"}
        )
    _write_text(tmp_path / "keep.txt", "current\n")
    watch = _BlobHashWatch(monkeypatch, tmp_path)
    caplog.set_level(logging.WARNING)

    store = WorkspaceSnapshotStore(tmp_path)
    [diff] = store.diff_snapshot("workspace_snapshot_2")

    assert diff.path == "keep.txt"
    assert diff.status == "modified"
    assert watch.snapshots_hashed() == {"workspace_snapshot_2"}
    warnings = _rehash_warnings(caplog)
    assert len(warnings) == 1
    assert "id=workspace_snapshot_2" in warnings[0]


def test_restoring_one_legacy_snapshot_rehashes_only_that_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
):
    for index in range(1, 4):
        _write_legacy_snapshot(
            tmp_path, f"workspace_snapshot_{index}", {"keep.txt": f"captured {index}\n"}
        )
    _write_text(tmp_path / "keep.txt", "current\n")
    _write_text(tmp_path / "added.txt", "added by the turn\n")
    watch = _BlobHashWatch(monkeypatch, tmp_path)
    caplog.set_level(logging.WARNING)

    store = WorkspaceSnapshotStore(tmp_path)
    restored = store.restore_snapshot("workspace_snapshot_3")

    assert (tmp_path / "keep.txt").read_bytes() == b"captured 3\n"
    assert not (tmp_path / "added.txt").exists()
    assert restored.file_count == 1
    assert watch.snapshots_hashed() == {"workspace_snapshot_3"}
    assert len(_rehash_warnings(caplog)) == 1


def test_legacy_format1_manifest_still_loads_and_restores(tmp_path: Path):
    _write_text(tmp_path / "keep.txt", "current\n")
    legacy_dir = _snapshot_dir(tmp_path, "workspace_snapshot_1")
    _write_text(legacy_dir / "files" / "keep.txt", "captured\n")
    manifest = {
        "snapshot_id": "workspace_snapshot_1",
        "workspace_root": str(tmp_path),
        "created_at": "2026-01-01T00:00:00+00:00",
        "label": "legacy",
        "metadata": {},
        "file_count": 1,
        "total_bytes": len(b"captured\n"),
    }
    (legacy_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    store = WorkspaceSnapshotStore(tmp_path)
    loaded = store.list_snapshots()
    assert [item.snapshot_id for item in loaded] == ["workspace_snapshot_1"]
    assert isinstance(loaded[0], WorkspaceSnapshot)

    store.restore_snapshot("workspace_snapshot_1")
    assert (tmp_path / "keep.txt").read_bytes() == b"captured\n"


def test_incomplete_legacy_snapshot_refuses_instead_of_wiping_the_workspace(
    tmp_path: Path,
):
    """A legacy snapshot that lost its blobs must not delete the workspace.

    Header-only loading trusts the manifest's declared counts. If the blobs
    later disagree (``shutil.rmtree(ignore_errors=True)`` in ``_remove_snapshot``
    walks bottom-up, so a partial failure can remove ``files/`` while leaving
    ``manifest.json``), reconciling the file map down to the surviving blobs
    would make ``restore_snapshot(delete_added=True)`` protect nothing and
    delete everything. It must raise instead.
    """
    from src.efp_runtime.workspace_snapshots import (
        WorkspaceSnapshotIncompleteError,
    )

    _write_legacy_snapshot(
        tmp_path, "workspace_snapshot_1", {"app.py": "orig\n", "README.md": "x\n"}
    )
    # Simulate the lost-blobs shape: manifest still declares 2 files, none remain.
    blobs = _snapshot_dir(tmp_path, "workspace_snapshot_1") / "files"
    for blob in list(blobs.rglob("*")):
        if blob.is_file():
            blob.unlink()

    _write_text(tmp_path / "app.py", "current\n")
    _write_text(tmp_path / "unrelated_new_work.py", "keep me\n")

    store = WorkspaceSnapshotStore(tmp_path)
    # Header still advertises the declared counts.
    assert store.list_snapshots()[0].file_count == 2

    with pytest.raises(WorkspaceSnapshotIncompleteError):
        store.restore_snapshot("workspace_snapshot_1", delete_added=True)

    # Nothing was touched: the workspace survives intact.
    assert (tmp_path / "app.py").read_bytes() == b"current\n"
    assert (tmp_path / "unrelated_new_work.py").read_bytes() == b"keep me\n"
