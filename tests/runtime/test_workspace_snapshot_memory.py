"""Memory-model tests for WorkspaceSnapshotStore.

The store must keep only metadata in RAM: content is streamed to/from the
disk-backed snapshot directory. These tests pin the behaviors introduced by
the memory fix: manifest-based (blob-free) reload, per-file size cap with
restore protection, heavy-directory exclusion, retention pruning, and
legacy (format-1) manifest compatibility.
"""

from __future__ import annotations

import json
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
    _write_text(tmp_path / "node_modules/pkg/index.js", "module.exports = 1\n")
    _write_text(tmp_path / ".venv/lib/site.py", "# venv\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert snapshot.file_count == 1

    store.restore_snapshot(snapshot.snapshot_id, delete_added=True)
    # Excluded directories are invisible to restore: never deleted as "added".
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
