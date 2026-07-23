from __future__ import annotations

import logging
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


def test_restore_snapshot_survives_new_store_instance(tmp_path: Path):
    _write_text(tmp_path / "app.py", "before\n")
    _write_text(tmp_path / "nested" / "data.txt", "data\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot(label="persisted", metadata={"turn": 1})

    _write_text(tmp_path / "app.py", "after\n")
    (tmp_path / "nested" / "data.txt").unlink()
    _write_text(tmp_path / "added.txt", "delete\n")

    restarted = WorkspaceSnapshotStore(tmp_path)
    loaded = restarted.list_snapshots()
    restored = restarted.restore_snapshot(snapshot.snapshot_id)
    next_snapshot = restarted.create_snapshot()

    assert [item.snapshot_id for item in loaded] == [snapshot.snapshot_id]
    assert loaded[0].label == "persisted"
    assert loaded[0].metadata == {"turn": 1}
    assert restored.snapshot_id == snapshot.snapshot_id
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "before\n"
    assert (tmp_path / "nested" / "data.txt").read_text(encoding="utf-8") == "data\n"
    assert not (tmp_path / "added.txt").exists()
    assert next_snapshot.snapshot_id != snapshot.snapshot_id


def test_delete_returns_true_and_unknown_snapshot_raises(tmp_path: Path):
    _write_text(tmp_path / "tracked.txt", "tracked\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert store.delete_snapshot(snapshot.snapshot_id) is True
    assert store.list_snapshots() == []
    with pytest.raises(KeyError, match=snapshot.snapshot_id):
        store.delete_snapshot(snapshot.snapshot_id)


def test_delete_snapshot_removes_persisted_snapshot_data(tmp_path: Path):
    _write_text(tmp_path / "tracked.txt", "tracked\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()
    snapshot_dir = (
        tmp_path
        / ".efp_runtime"
        / "workspace_snapshots"
        / snapshot.snapshot_id
    )

    assert snapshot_dir.is_dir()
    assert store.delete_snapshot(snapshot.snapshot_id) is True
    assert not snapshot_dir.exists()

    restarted = WorkspaceSnapshotStore(tmp_path)
    assert restarted.list_snapshots() == []
    with pytest.raises(KeyError, match=snapshot.snapshot_id):
        restarted.restore_snapshot(snapshot.snapshot_id)


def test_restore_ignores_excluded_directories_and_does_not_delete_them(
    tmp_path: Path,
):
    excluded_paths = [
        tmp_path / ".git" / "config",
        tmp_path / ".efp" / "runtime" / "sessions" / "session.json",
        tmp_path / ".efp" / "runtime_tasks" / "task.json",
        tmp_path / ".efp_runtime" / "state.json",
        tmp_path / "__pycache__" / "module.pyc",
        tmp_path / ".pytest_cache" / "state",
        tmp_path / "node_modules" / "pkg" / "index.js",
        tmp_path / "nested" / "node_modules" / "index.js",
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


def test_snapshot_excludes_runtime_owned_state_directories(tmp_path: Path):
    """`.efp/runtime` and `.efp/runtime_tasks` are runtime-owned, not content.

    Everything else under `.efp` (config, skills, commands, ...) is ordinary
    agent-editable workspace content and must stay capturable.
    """
    _write_text(tmp_path / "app.py", "base\n")
    _write_text(tmp_path / ".efp" / "runtime" / "sessions" / "s.json", "{}\n")
    _write_text(tmp_path / ".efp" / "runtime_tasks" / "task-1.json", "{}\n")
    _write_text(tmp_path / ".efp" / "config.json", "{}\n")
    # A nested directory with the same *name* is ordinary content: only the
    # workspace-relative paths are runtime-owned.
    _write_text(tmp_path / "src" / "runtime_tasks" / "keep.py", "keep\n")

    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()
    captured = {
        item.path
        for item in store._require_snapshot(snapshot.snapshot_id).files.values()
    }

    assert captured == {
        "app.py",
        ".efp/config.json",
        "src/runtime_tasks/keep.py",
    }
    assert snapshot.file_count == 3


def test_restore_does_not_delete_the_live_runtime_task_store(tmp_path: Path):
    """`.efp/runtime_tasks` holds live background tasks; a revert must not touch it."""
    _write_text(tmp_path / "app.py", "base\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    task_path = tmp_path / ".efp" / "runtime_tasks" / "task-1.json"
    _write_text(task_path, '{"status": "running"}\n')

    store.restore_snapshot(snapshot.snapshot_id)

    assert task_path.read_text(encoding="utf-8") == '{"status": "running"}\n'


def test_restore_of_an_older_snapshot_does_not_rewrite_the_live_task_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Snapshots taken before `.efp/runtime_tasks` was runtime-owned hold its blobs.

    Writing those back on the first revert after a deploy drops a stale
    background-task queue on top of the running one.
    """
    from efp_runtime import workspace_snapshots

    task_path = tmp_path / ".efp" / "runtime_tasks" / "task-1.json"
    _write_text(tmp_path / "app.py", "base\n")
    _write_text(task_path, '{"status": "queued"}\n')

    # Capture the way the pre-deploy build did: nothing runtime-owned excluded.
    monkeypatch.setattr(
        workspace_snapshots, "_ALWAYS_EXCLUDED_RELATIVE_DIRS", frozenset()
    )
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()
    monkeypatch.undo()

    assert ".efp/runtime_tasks/task-1.json" in set(
        store._require_snapshot(snapshot.snapshot_id).files
    )

    _write_text(task_path, '{"status": "running"}\n')
    _write_text(tmp_path / "app.py", "changed\n")
    WorkspaceSnapshotStore(tmp_path).restore_snapshot(snapshot.snapshot_id)

    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "base\n"
    assert task_path.read_text(encoding="utf-8") == '{"status": "running"}\n'


def test_log_directories_are_captured_by_default(tmp_path: Path):
    """`logs/` is legitimately committed (logs/.gitkeep, a `logs` package)."""
    _write_text(tmp_path / "logs" / ".gitkeep", "")
    _write_text(tmp_path / "src" / "logs" / "__init__.py", "before\n")

    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert set(store._require_snapshot(snapshot.snapshot_id).files) == {
        "logs/.gitkeep",
        "src/logs/__init__.py",
    }
    assert snapshot.excluded_directories == []

    _write_text(tmp_path / "src" / "logs" / "__init__.py", "after\n")
    store.restore_snapshot(snapshot.snapshot_id)

    assert (tmp_path / "src" / "logs" / "__init__.py").read_text(
        encoding="utf-8"
    ) == "before\n"


def test_capture_creates_each_destination_directory_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The mirrored directory is created once per source dir, not per file."""
    for index in range(5):
        _write_text(tmp_path / "pkg" / f"file{index}.txt", f"content {index}\n")

    store = WorkspaceSnapshotStore(tmp_path)

    made: list[Path] = []
    original_mkdir = Path.mkdir

    def counting_mkdir(self: Path, *args, **kwargs):
        made.append(Path(self))
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", counting_mkdir)
    snapshot = store.create_snapshot()
    monkeypatch.undo()

    mirror = (
        tmp_path
        / ".efp_runtime"
        / "workspace_snapshots"
        / snapshot.snapshot_id
        / "files"
        / "pkg"
    )

    assert snapshot.file_count == 5
    assert made.count(mirror) == 1
    for index in range(5):
        assert (mirror / f"file{index}.txt").is_file()


def test_capture_leaves_no_mirror_directory_when_nothing_is_captured(
    tmp_path: Path,
):
    _write_text(tmp_path / "keep.txt", "keep\n")
    _write_text(tmp_path / "node_modules" / "dep.js", "excluded\n")
    _write_text(tmp_path / "huge" / "big.bin", "x" * 64)

    store = WorkspaceSnapshotStore(tmp_path, max_file_bytes=16)
    snapshot = store.create_snapshot()
    files_dir = (
        tmp_path
        / ".efp_runtime"
        / "workspace_snapshots"
        / snapshot.snapshot_id
        / "files"
    )

    assert (files_dir / "keep.txt").is_file()
    assert not (files_dir / "node_modules").exists()
    assert not (files_dir / "huge").exists()


def test_create_snapshot_logs_one_timing_line(tmp_path: Path, caplog):
    _write_text(tmp_path / "alpha.txt", "abc")
    _write_text(tmp_path / "big.bin", "x" * 64)
    store = WorkspaceSnapshotStore(tmp_path, max_file_bytes=16)

    caplog.set_level(logging.INFO)
    snapshot = store.create_snapshot()

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("workspace_snapshot.created")
    ]

    assert len(lines) == 1
    line = lines[0]
    assert "\n" not in line
    assert f"id={snapshot.snapshot_id}" in line
    assert f"root={store.workspace_root}" in line
    assert "files=1" in line
    assert "skipped=1" in line
    assert "bytes=3" in line
    for field in ("reload_ms=", "capture_ms=", "prune_ms=", "total_ms="):
        assert field in line


def test_legacy_manifest_rehash_is_logged_as_a_warning(tmp_path: Path, caplog):
    import json

    _write_text(tmp_path / "alpha.txt", "abc")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    manifest_path = (
        tmp_path
        / ".efp_runtime"
        / "workspace_snapshots"
        / snapshot.snapshot_id
        / "manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["format"] = 1
    payload.pop("files")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    caplog.set_level(logging.WARNING)
    reloaded = WorkspaceSnapshotStore(tmp_path)

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("workspace_snapshot.legacy_manifest_rehash")
    ]

    assert [item.snapshot_id for item in reloaded.list_snapshots()] == [
        snapshot.snapshot_id
    ]
    assert len(warnings) >= 1
    assert f"id={snapshot.snapshot_id}" in warnings[0]
    assert "format=1" in warnings[0]
    assert "files=1" in warnings[0]
    assert "elapsed_ms=" in warnings[0]


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


def test_efp_workspace_content_is_captured_diffed_and_restored(tmp_path: Path):
    """Only `.efp/runtime` is runtime-owned; the rest of `.efp` is user data."""
    skill = tmp_path / ".efp" / "skills" / "my-skill" / "SKILL.md"
    config = tmp_path / ".efp" / "config.json"
    _write_text(skill, "# original skill\n")
    _write_text(config, '{"model": "before"}\n')
    _write_text(tmp_path / ".efp" / "commands" / "go.md", "go\n")
    _write_text(tmp_path / ".efp" / "agents" / "helper.md", "helper\n")
    _write_text(
        tmp_path / ".efp" / "instructions" / "style.instructions.md", "style\n"
    )
    _write_text(tmp_path / ".efp" / "runtime" / "sessions" / "s.json", "{}\n")

    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    captured = set(store._require_snapshot(snapshot.snapshot_id).files)
    assert captured == {
        ".efp/skills/my-skill/SKILL.md",
        ".efp/config.json",
        ".efp/commands/go.md",
        ".efp/agents/helper.md",
        ".efp/instructions/style.instructions.md",
    }

    # An agent rewrites the skill and the config, and drops a new file in.
    _write_text(skill, "# corrupted\n")
    _write_text(config, '{"model": "after"}\n')
    _write_text(tmp_path / ".efp" / "skills" / "evil" / "x", "evil\n")

    diffs = {
        item.path: item.status for item in store.diff_snapshot(snapshot.snapshot_id)
    }
    assert diffs[".efp/skills/my-skill/SKILL.md"] == "modified"
    assert diffs[".efp/config.json"] == "modified"
    assert diffs[".efp/skills/evil/x"] == "added"

    store.restore_snapshot(snapshot.snapshot_id)

    assert skill.read_text(encoding="utf-8") == "# original skill\n"
    assert config.read_text(encoding="utf-8") == '{"model": "before"}\n'
    assert not (tmp_path / ".efp" / "skills" / "evil" / "x").exists()


def test_session_store_under_efp_is_never_captured_or_deleted(tmp_path: Path):
    session_file = tmp_path / ".efp" / "runtime" / "sessions" / "s.json"
    _write_text(tmp_path / ".efp" / "config.json", "{}\n")
    _write_text(session_file, '{"before": true}\n')

    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert ".efp/runtime/sessions/s.json" not in set(
        store._require_snapshot(snapshot.snapshot_id).files
    )

    _write_text(session_file, '{"after": true}\n')
    added_session_file = tmp_path / ".efp" / "runtime" / "sessions" / "new.json"
    _write_text(added_session_file, "{}\n")

    assert not [
        item
        for item in store.diff_snapshot(snapshot.snapshot_id)
        if item.path.startswith(".efp/runtime/")
    ]

    store.restore_snapshot(snapshot.snapshot_id, delete_added=True)

    # Neither reverted nor deleted: the runtime owns this tree.
    assert session_file.read_text(encoding="utf-8") == '{"after": true}\n'
    assert added_session_file.exists()


def test_nested_efp_directory_is_ordinary_workspace_content(tmp_path: Path):
    """Exclusion is path-scoped, so a `.efp` at any other depth is captured."""
    keep = tmp_path / "docs" / ".efp" / "keep.txt"
    nested_runtime = tmp_path / "docs" / ".efp" / "runtime" / "notes.txt"
    _write_text(keep, "keep\n")
    _write_text(nested_runtime, "also kept\n")

    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()
    captured = set(store._require_snapshot(snapshot.snapshot_id).files)

    assert captured == {"docs/.efp/keep.txt", "docs/.efp/runtime/notes.txt"}

    _write_text(keep, "changed\n")
    store.restore_snapshot(snapshot.snapshot_id)

    assert keep.read_text(encoding="utf-8") == "keep\n"


def test_snapshot_reports_files_skipped_for_size(tmp_path: Path):
    _write_text(tmp_path / "small.txt", "ok\n")
    _write_text(tmp_path / "big.bin", "x" * 64)

    store = WorkspaceSnapshotStore(tmp_path, max_file_bytes=16)
    snapshot = store.create_snapshot()

    assert snapshot.skipped_files == {"big.bin": 64}
    assert set(store.list_snapshots()[0].skipped_files) == {"big.bin"}

    # The size-capped file is unrestorable, and the restore result says so.
    _write_text(tmp_path / "big.bin", "y" * 64)
    restored = store.restore_snapshot(snapshot.snapshot_id)

    assert restored.skipped_files == {"big.bin": 64}
    assert (tmp_path / "big.bin").read_text(encoding="utf-8") == "y" * 64


def test_snapshot_reports_excluded_directories_present_at_capture(tmp_path: Path):
    _write_text(tmp_path / "app.py", "base\n")
    _write_text(tmp_path / "node_modules" / "pkg" / "index.js", "dep\n")
    _write_text(tmp_path / "src" / ".cache" / "run.bin", "noise\n")

    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert snapshot.excluded_directories == ["node_modules", "src/.cache"]
    # Survives a restart and reaches the caller through restore_snapshot.
    restarted = WorkspaceSnapshotStore(tmp_path)
    assert restarted.list_snapshots()[0].excluded_directories == [
        "node_modules",
        "src/.cache",
    ]
    assert restarted.restore_snapshot(snapshot.snapshot_id).excluded_directories == [
        "node_modules",
        "src/.cache",
    ]


def test_build_output_directories_are_skipped_but_reported(tmp_path: Path):
    """Heavy build dirs stay out of the capture, and say so.

    Capturing them is a latency/disk budget, not a preference: create_snapshot
    runs synchronously on the request path against a network PVC once per turn.
    Measured on a modest tree, including them cost 24x the time and 61x the
    bytes. Some repos do commit ``vendor/`` or build output, so the honest
    answer is to record what was skipped and let revert report a partial
    restore -- not to silently drop the files, and not to copy a GB-scale
    ``target/`` on every message.
    """
    names = ("vendor", "build", "dist", "target", "coverage")
    for name in names:
        _write_text(tmp_path / name / "tracked.txt", "before\n")
    _write_text(tmp_path / "app.py", "base\n")

    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert set(store._require_snapshot(snapshot.snapshot_id).files) == {"app.py"}
    # The skip is recorded, so a caller can tell the user what was not covered.
    assert set(snapshot.excluded_directories) == {f"{name}" for name in names}

    # A skipped directory is never deleted as an "added" file either.
    for name in names:
        _write_text(tmp_path / name / "tracked.txt", "after\n")
    store.restore_snapshot(snapshot.snapshot_id, delete_added=True)

    for name in names:
        assert (tmp_path / name / "tracked.txt").read_text(
            encoding="utf-8"
        ) == "after\n"


def test_excluded_directory_names_are_configurable(tmp_path: Path):
    _write_text(tmp_path / "app.py", "base\n")
    _write_text(tmp_path / "vendor" / "dep.go", "before\n")
    _write_text(tmp_path / "node_modules" / "pkg.js", "dep\n")

    store = WorkspaceSnapshotStore(tmp_path, excluded_directory_names={"vendor"})
    snapshot = store.create_snapshot()
    captured = set(store._require_snapshot(snapshot.snapshot_id).files)

    assert captured == {"app.py", "node_modules/pkg.js"}
    assert snapshot.excluded_directories == ["vendor"]

    _write_text(tmp_path / "vendor" / "dep.go", "after\n")
    store.restore_snapshot(snapshot.snapshot_id)

    assert (tmp_path / "vendor" / "dep.go").read_text(encoding="utf-8") == "after\n"


def test_restore_never_deletes_files_under_a_recorded_excluded_directory(
    tmp_path: Path,
):
    """A directory the capture skipped is out of scope, not "added by the turn".

    The scan that finds "added" files uses the *current* exclusion policy, so a
    directory excluded only at capture time looks entirely new on restore.
    """
    _write_text(tmp_path / "app.py", "base\n")
    _write_text(tmp_path / "vendor" / "github.com" / "x" / "x.go", "before\n")
    capturing_store = WorkspaceSnapshotStore(
        tmp_path, excluded_directory_names={"vendor"}
    )
    snapshot = capturing_store.create_snapshot()

    assert snapshot.excluded_directories == ["vendor"]

    # A later store (or a later default) no longer excludes vendor/, so its
    # files show up in the "added" scan.
    restoring_store = WorkspaceSnapshotStore(tmp_path, excluded_directory_names=set())
    _write_text(tmp_path / "new.py", "delete me\n")
    restoring_store.restore_snapshot(snapshot.snapshot_id)

    assert not (tmp_path / "new.py").exists()
    assert (tmp_path / "vendor" / "github.com" / "x" / "x.go").read_text(
        encoding="utf-8"
    ) == "before\n"


def test_restore_from_a_manifest_without_excluded_directories_keeps_historic_dirs(
    tmp_path: Path,
):
    """A manifest that records no exclusions cannot prove a path was ever in scope.

    Format-1 manifests (and format-2 ones written before the key existed) have
    no ``excluded_directories``, so every file under a historically excluded
    name looks "added" and would be deleted.
    """
    import json

    _write_text(tmp_path / "app.py", "base\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    manifest_path = (
        tmp_path
        / ".efp_runtime"
        / "workspace_snapshots"
        / snapshot.snapshot_id
        / "manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["format"] = 1
    payload.pop("files")
    payload.pop("excluded_directories")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    survivors = [
        tmp_path / "vendor" / "github.com" / "x" / "x.go",
        tmp_path / "dist" / "bundle.js",
        tmp_path / "build" / "out.o",
        tmp_path / "target" / "debug" / "app",
        tmp_path / "coverage" / "index.html",
        tmp_path / "src" / "node_modules" / "pkg" / "index.js",
        tmp_path / ".efp" / "runtime_tasks" / "task-1.json",
    ]
    for path in survivors:
        _write_text(path, "live\n")
    _write_text(tmp_path / "new.py", "delete me\n")

    legacy_store = WorkspaceSnapshotStore(tmp_path)
    legacy_store.restore_snapshot(snapshot.snapshot_id)

    assert not (tmp_path / "new.py").exists()
    for path in survivors:
        assert path.read_text(encoding="utf-8") == "live\n"


def test_restore_from_a_manifest_recording_no_exclusions_still_deletes_added_files(
    tmp_path: Path,
):
    """An empty (but present) ``excluded_directories`` is trustworthy evidence."""
    _write_text(tmp_path / "app.py", "base\n")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    assert snapshot.excluded_directories == []

    # Not under an excluded directory, so it really is an added file.
    _write_text(tmp_path / "src" / "bundle.js", "added by the turn\n")
    reloaded = WorkspaceSnapshotStore(tmp_path)
    reloaded.restore_snapshot(snapshot.snapshot_id)

    assert not (tmp_path / "src" / "bundle.js").exists()


def test_retained_set_outgrowing_the_cap_is_logged_once_per_transition(
    tmp_path: Path, caplog
):
    """Retention beats the cap, so the overshoot must be visible — but once.

    Pruning runs several times per turn; repeating the warning on every prune
    turns a one-off condition into an endless log stream.
    """
    _write_text(tmp_path / "app.py", "base\n")
    store = WorkspaceSnapshotStore(
        tmp_path,
        max_retained_snapshots=1,
        retain_snapshots=lambda snapshots: [item.snapshot_id for item in snapshots],
    )

    caplog.set_level(logging.WARNING)
    store.create_snapshot(label="one")
    store.create_snapshot(label="two")
    store.create_snapshot(label="three")
    store.create_snapshot(label="four")

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("workspace_snapshot.retention_exceeds_cap")
    ]

    assert len(store.list_snapshots()) == 4
    assert len(warnings) == 1
    assert "retained=2" in warnings[0]
    assert "cap=1" in warnings[0]


def test_retained_set_within_the_cap_is_not_logged(tmp_path: Path, caplog):
    _write_text(tmp_path / "app.py", "base\n")
    store = WorkspaceSnapshotStore(
        tmp_path,
        max_retained_snapshots=4,
        retain_snapshots=lambda snapshots: [item.snapshot_id for item in snapshots],
    )

    caplog.set_level(logging.WARNING)
    for index in range(3):
        store.create_snapshot(label=f"snapshot-{index}")

    assert not [
        record
        for record in caplog.records
        if record.getMessage().startswith("workspace_snapshot.retention_exceeds_cap")
    ]


def test_legacy_manifest_rehash_warns_once_per_manifest(tmp_path: Path, caplog):
    """The rehash warning must not fire again on every runtime operation."""
    import json

    _write_text(tmp_path / "alpha.txt", "abc")
    store = WorkspaceSnapshotStore(tmp_path)
    snapshot = store.create_snapshot()

    manifest_path = (
        tmp_path
        / ".efp_runtime"
        / "workspace_snapshots"
        / snapshot.snapshot_id
        / "manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["format"] = 1
    payload.pop("files")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    caplog.set_level(logging.WARNING)
    reloaded = WorkspaceSnapshotStore(tmp_path)
    for _ in range(5):
        reloaded.list_snapshots()
        reloaded.diff_snapshot(snapshot.snapshot_id)

    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("workspace_snapshot.legacy_manifest_rehash")
    ]

    assert len(warnings) == 1


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
