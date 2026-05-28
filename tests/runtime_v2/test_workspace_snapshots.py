from __future__ import annotations

from pathlib import Path

import pytest

from efp_runtime import WorkspaceSnapshotStore
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig


def test_snapshot_captures_file_count_and_total_bytes(tmp_path: Path):
    _write_text(tmp_path / "alpha.txt", "abc")
    _write_text(tmp_path / "nested" / "beta.txt", "de")
    _write_text(tmp_path / ".git" / "config", "ignored")

    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot(label="base", metadata={"phase": 27})

    assert snapshot.workspace_root == tmp_path.resolve()
    assert snapshot.file_count == 2
    assert snapshot.total_bytes == 5
    assert snapshot.label == "base"
    assert snapshot.metadata == {"phase": 27}
    assert [item.snapshot_id for item in store.list_snapshots()] == [
        snapshot.snapshot_id
    ]


def test_diff_reports_modified_added_deleted_and_patch_counts(tmp_path: Path):
    _write_text(tmp_path / "notes.txt", "one\nsame\n")
    _write_text(tmp_path / "removed.txt", "remove me\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    _write_text(tmp_path / "notes.txt", "one\nchanged\nextra\n")
    _write_text(tmp_path / "added.txt", "new\n")
    (tmp_path / "removed.txt").unlink()

    diffs = {item.path: item for item in store.diff_snapshot(snapshot.snapshot_id)}

    assert {item.status for item in diffs.values()} == {
        "added",
        "deleted",
        "modified",
    }
    assert diffs["added.txt"].status == "added"
    assert diffs["removed.txt"].status == "deleted"
    modified = diffs["notes.txt"]
    assert modified.status == "modified"
    assert modified.before_hash != modified.after_hash
    assert modified.before_bytes == len("one\nsame\n".encode("utf-8"))
    assert modified.after_bytes == len("one\nchanged\nextra\n".encode("utf-8"))
    assert modified.patch is not None
    assert "--- a/notes.txt" in modified.patch
    assert "+++ b/notes.txt" in modified.patch
    assert "-same" in modified.patch
    assert "+changed" in modified.patch
    assert "+extra" in modified.patch
    assert modified.additions == 2
    assert modified.deletions == 1

    with pytest.raises(KeyError, match="missing-snapshot"):
        store.diff_snapshot("missing-snapshot")


def test_restore_brings_files_back_and_removes_added_by_default(tmp_path: Path):
    _write_text(tmp_path / "keep.txt", "before\n")
    _write_text(tmp_path / "removed.txt", "restore\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    _write_text(tmp_path / "keep.txt", "after\n")
    (tmp_path / "removed.txt").unlink()
    _write_text(tmp_path / "added.txt", "delete\n")

    restored = store.restore_snapshot(snapshot.snapshot_id)

    assert restored.snapshot_id == snapshot.snapshot_id
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "before\n"
    assert (tmp_path / "removed.txt").read_text(encoding="utf-8") == "restore\n"
    assert not (tmp_path / "added.txt").exists()


def test_restore_delete_added_false_preserves_added_files(tmp_path: Path):
    _write_text(tmp_path / "tracked.txt", "tracked\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()
    _write_text(tmp_path / "added.txt", "keep\n")

    store.restore_snapshot(snapshot.snapshot_id, delete_added=False)

    assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "tracked\n"
    assert (tmp_path / "added.txt").read_text(encoding="utf-8") == "keep\n"


def test_delete_returns_true_and_unknown_snapshot_raises(tmp_path: Path):
    _write_text(tmp_path / "tracked.txt", "tracked\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert store.delete_snapshot(snapshot.snapshot_id) is True
    assert store.list_snapshots() == []
    with pytest.raises(KeyError, match=snapshot.snapshot_id):
        store.delete_snapshot(snapshot.snapshot_id)


def test_restore_ignores_excluded_directories_and_does_not_delete_them(
    tmp_path: Path,
):
    excluded_paths = [
        tmp_path / ".git" / "config",
        tmp_path / ".efp_runtime" / "state.json",
        tmp_path / "__pycache__" / "module.pyc",
        tmp_path / ".pytest_cache" / "state",
    ]
    _write_text(tmp_path / "app.py", "base\n")
    for path in excluded_paths:
        _write_text(path, "before\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert snapshot.file_count == 1

    _write_text(tmp_path / "app.py", "changed\n")
    _write_text(tmp_path / "new.py", "delete\n")
    for path in excluded_paths:
        _write_text(path, "after\n")

    store.restore_snapshot(snapshot.snapshot_id)

    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "base\n"
    assert not (tmp_path / "new.py").exists()
    for path in excluded_paths:
        assert path.read_text(encoding="utf-8") == "after\n"


def test_snapshot_skips_symlinks_and_restore_does_not_follow_file_symlink(
    tmp_path: Path,
):
    outside = tmp_path.with_name(f"{tmp_path.name}_outside")
    outside.mkdir()
    external = outside / "external.txt"
    _write_text(external, "external\n")
    _write_text(tmp_path / "tracked.txt", "tracked\n")
    try:
        (tmp_path / "linked-file").symlink_to(external)
        (tmp_path / "linked-dir").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()
    (tmp_path / "tracked.txt").unlink()
    (tmp_path / "tracked.txt").symlink_to(external)

    store.restore_snapshot(snapshot.snapshot_id)

    assert snapshot.file_count == 1
    assert external.read_text(encoding="utf-8") == "external\n"
    assert not (tmp_path / "tracked.txt").is_symlink()
    assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "tracked\n"


def test_agent_runtime_workspace_snapshot_methods_require_workspace_root():
    runtime = AgentRuntime(provider=ScriptedLLMProvider([]))

    with pytest.raises(TypeError, match="workspace snapshots require workspace_root"):
        runtime.create_workspace_snapshot()
    with pytest.raises(TypeError, match="workspace snapshots require workspace_root"):
        runtime.list_workspace_snapshots()
    with pytest.raises(TypeError, match="workspace snapshots require workspace_root"):
        runtime.diff_workspace_snapshot("snapshot")
    with pytest.raises(TypeError, match="workspace snapshots require workspace_root"):
        runtime.restore_workspace_snapshot("snapshot")
    with pytest.raises(TypeError, match="workspace snapshots require workspace_root"):
        runtime.delete_workspace_snapshot("snapshot")


def test_agent_runtime_workspace_snapshot_methods_work_when_configured(
    tmp_path: Path,
):
    _write_text(tmp_path / "runtime.txt", "before\n")
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(workspace_root=tmp_path),
    )

    snapshot = runtime.create_workspace_snapshot(label="runtime")
    _write_text(tmp_path / "runtime.txt", "after\n")
    diffs = runtime.diff_workspace_snapshot(snapshot.snapshot_id)
    restored = runtime.restore_workspace_snapshot(snapshot.snapshot_id)

    assert [item.snapshot_id for item in runtime.list_workspace_snapshots()] == [
        snapshot.snapshot_id
    ]
    assert restored.snapshot_id == snapshot.snapshot_id
    assert diffs[0].path == "runtime.txt"
    assert diffs[0].status == "modified"
    assert (tmp_path / "runtime.txt").read_text(encoding="utf-8") == "before\n"
    assert runtime.delete_workspace_snapshot(snapshot.snapshot_id) is True
    with pytest.raises(KeyError, match=snapshot.snapshot_id):
        runtime.delete_workspace_snapshot(snapshot.snapshot_id)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
