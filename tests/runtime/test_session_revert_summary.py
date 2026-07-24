from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import Message, MessagePart, MessageRole, ToolResult
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.summary import (
    collect_session_file_diffs,
    summarize_session_diffs,
)


def test_summarize_session_diffs_collects_metadata_output_and_source_ids():
    duplicate = {
        "path": "alpha.txt",
        "old_path": "alpha.txt",
        "additions": 2,
        "deletions": 1,
        "patch": "@@ alpha",
        "metadata": {"kind": "metadata"},
    }
    output_only = {
        "path": "beta.txt",
        "old_path": "beta.txt",
        "additions": "bad",
        "deletions": None,
        "patch": "@@ beta",
    }
    messages = [
        Message.from_text(MessageRole.USER, "before", message_id="msg-before"),
        Message(
            role=MessageRole.TOOL,
            message_id="msg-tool",
            parts=[
                MessagePart.tool_result_part(
                    ToolResult(
                        call_id="call-write",
                        tool_name="write",
                        output={
                            "filediff": duplicate,
                            "filediffs": [duplicate, output_only],
                        },
                        metadata={
                            "filediff": duplicate,
                            "filediffs": [duplicate],
                        },
                    ),
                    part_id="part-result",
                )
            ],
        ),
    ]

    diffs = collect_session_file_diffs(messages, message_id="msg-tool")
    summary = summarize_session_diffs(messages, message_id="msg-tool")

    assert len(diffs) == 2
    assert diffs[0]["path"] == "alpha.txt"
    assert diffs[0]["source_message_id"] == "msg-tool"
    assert diffs[0]["source_part_id"] == "part-result"
    assert diffs[0]["source_tool_call_id"] == "call-write"
    assert diffs[0]["metadata"] == {"kind": "metadata"}
    assert diffs[1]["path"] == "beta.txt"
    assert diffs[1]["additions"] == 0
    assert diffs[1]["deletions"] == 0
    assert summary["diff_count"] == 2
    assert summary["file_count"] == 2
    assert summary["files"] == ["alpha.txt", "beta.txt"]
    assert summary["additions"] == 2
    assert summary["deletions"] == 1


@pytest.mark.asyncio
async def test_runtime_session_revert_and_unrevert_restore_history_and_workspace(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-write",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {
                                    "filePath": "created.txt",
                                    "content": "created\n",
                                },
                                sort_keys=True,
                            ),
                        },
                    }
                ]
            },
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )

    result = await runtime.run("Create a file.", session_id="session-revert")

    assert result.status == LoopStatus.COMPLETED
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created\n"
    session = runtime.get_session("session-revert")
    assert session.metadata["revert"]["active"] is False
    assert session.metadata["revert"]["source"] == "run"
    assert session.metadata["revert"]["workspace_snapshot_id"]
    assert session.metadata["summary"]["files"] == ["created.txt"]
    assert session.metadata["summary"]["additions"] == 1
    assert len(runtime.session_messages("session-revert")) == 4

    reverted = runtime.revert_session("session-revert")

    assert (tmp_path / "created.txt").exists() is False
    assert runtime.session_messages("session-revert") == []
    assert reverted.metadata["revert"]["active"] is True
    assert reverted.metadata["revert"]["removed_message_count"] == 4
    assert reverted.metadata["revert"]["history_checkpoint_id"]
    assert reverted.metadata["revert"]["unrevert_snapshot_id"]

    unreverted = runtime.unrevert_session("session-revert")

    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created\n"
    assert len(runtime.session_messages("session-revert")) == 4
    assert unreverted.metadata["revert"]["active"] is False
    assert unreverted.metadata["revert"]["status"] == "unreverted"


@pytest.mark.asyncio
async def test_runtime_session_revert_preserves_oldest_target_at_retention_limit(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-write",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {
                                    "filePath": "created.txt",
                                    "content": "created\n",
                                },
                                sort_keys=True,
                            ),
                        },
                    }
                ]
            },
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )
    assert runtime.workspace_snapshot_store is not None
    runtime.workspace_snapshot_store.max_retained_snapshots = 2

    await runtime.run("Create a file.", session_id="session-retention-revert")
    target_snapshot_id = runtime.get_session("session-retention-revert").metadata[
        "revert"
    ]["workspace_snapshot_id"]
    runtime.create_workspace_snapshot(label="newer-session-snapshot")

    reverted = runtime.revert_session("session-retention-revert")

    assert not (tmp_path / "created.txt").exists()
    assert reverted.metadata["revert"]["workspace_snapshot_id"] == target_snapshot_id
    unrevert_snapshot_id = reverted.metadata["revert"]["unrevert_snapshot_id"]
    assert unrevert_snapshot_id
    assert {
        snapshot.snapshot_id for snapshot in runtime.list_workspace_snapshots()
    } == {target_snapshot_id, unrevert_snapshot_id}


@pytest.mark.asyncio
async def test_runtime_pruning_keeps_recent_session_revert_targets(
    tmp_path: Path,
):
    """Recent sessions keep their revert target; older ones age out.

    Retention beats the retained-snapshot cap for recent sessions on purpose:
    dropping the BEFORE snapshot turns a later revert into a silent
    history-only trim. It is bounded by the cap so that it cannot grow with
    lifetime session count.
    """
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider(
            [{"content": "first"}, {"content": "second"}, {"content": "third"}]
        ),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            model_aware_tool_selection=False,
        ),
    )
    assert runtime.workspace_snapshot_store is not None
    runtime.workspace_snapshot_store.max_retained_snapshots = 2

    await runtime.run("First", session_id="session-first")
    first_snapshot_id = runtime.get_session("session-first").metadata["revert"][
        "workspace_snapshot_id"
    ]
    await runtime.run("Second", session_id="session-second")
    second_snapshot_id = runtime.get_session("session-second").metadata["revert"][
        "workspace_snapshot_id"
    ]
    runtime.store.list_sessions = lambda: pytest.fail(
        "snapshot pruning must not load every session body"
    )
    await runtime.run("Third", session_id="session-third")
    third_snapshot_id = runtime.get_session("session-third").metadata["revert"][
        "workspace_snapshot_id"
    ]

    retained = {
        snapshot.snapshot_id for snapshot in runtime.list_workspace_snapshots()
    }

    assert retained == {second_snapshot_id, third_snapshot_id}
    assert first_snapshot_id not in retained
    # The two newest sessions are still fully revertible.
    reverted = runtime.revert_session("session-second")
    assert reverted.metadata["revert"]["status"] == "reverted"
    assert reverted.metadata["revert"]["workspace_restore_status"] == "restored"


@pytest.mark.asyncio
async def test_runtime_retention_of_session_revert_targets_is_bounded(
    tmp_path: Path,
):
    """Retention scales with the cap, not with lifetime session count.

    ``finalize_session_revert_record`` publishes a revert target on every
    finished turn, so pinning one per session that has ever run grows the
    on-disk snapshot store forever on a long-lived gateway.
    """
    cap = 3
    older_sessions = 8
    responses: list[dict] = [
        {"content": f"turn-{index}"} for index in range(older_sessions)
    ]
    responses.extend(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-write-bounded",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {"filePath": "created.txt", "content": "created\n"},
                                sort_keys=True,
                            ),
                        },
                    }
                ]
            },
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider(responses),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )
    assert runtime.workspace_snapshot_store is not None
    runtime.workspace_snapshot_store.max_retained_snapshots = cap

    for index in range(older_sessions):
        await runtime.run("Turn", session_id=f"session-{index}")

    assert len(runtime.list_workspace_snapshots()) <= cap

    await runtime.run("Create a file.", session_id="session-recent")

    assert (tmp_path / "created.txt").exists()
    assert len(runtime.list_workspace_snapshots()) <= cap

    # The turn that just finished is still revertible for real.
    reverted = runtime.revert_session("session-recent")

    assert reverted.metadata["revert"]["status"] == "reverted"
    assert reverted.metadata["revert"]["workspace_restore_status"] == "restored"
    assert not (tmp_path / "created.txt").exists()

    # A session far outside the window lost its target and says so instead of
    # reporting a complete revert.
    aged_out = runtime.revert_session("session-0")

    assert aged_out.metadata["revert"]["status"] == "reverted_history_only"
    assert aged_out.metadata["revert"]["workspace_restored"] is False


@pytest.mark.asyncio
async def test_runtime_pruning_invalidates_expired_unrevert_snapshot_reference(
    tmp_path: Path,
):
    """A snapshot nothing references any more is pruned and de-referenced."""
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "first"}]),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            model_aware_tool_selection=False,
        ),
    )
    assert runtime.workspace_snapshot_store is not None
    runtime.workspace_snapshot_store.max_retained_snapshots = 2

    await runtime.run("First", session_id="session-expiring")
    runtime.revert_session("session-expiring")
    runtime.unrevert_session("session-expiring")
    unrevert_snapshot_id = runtime.get_session("session-expiring").metadata["revert"][
        "unrevert_snapshot_id"
    ]
    assert unrevert_snapshot_id

    runtime.store.list_sessions = lambda: pytest.fail(
        "snapshot pruning must not load every session body"
    )
    runtime.create_workspace_snapshot(label="newer-one")
    runtime.create_workspace_snapshot(label="newer-two")

    assert unrevert_snapshot_id not in {
        snapshot.snapshot_id for snapshot in runtime.list_workspace_snapshots()
    }
    assert (
        runtime.get_session("session-expiring").metadata["revert"][
            "unrevert_snapshot_id"
        ]
        is None
    )


@pytest.mark.asyncio
async def test_runtime_retention_protects_snapshot_until_run_finalizes(
    tmp_path: Path,
):
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingProvider:
        async def invoke(self, request):
            started.set()
            await release.wait()
            return {"content": "done"}

    runtime = AgentRuntime(
        provider=BlockingProvider(),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            model_aware_tool_selection=False,
        ),
    )
    competing_runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            model_aware_tool_selection=False,
        ),
    )
    assert runtime.workspace_snapshot_store is not None
    assert competing_runtime.workspace_snapshot_store is not None
    runtime.workspace_snapshot_store.max_retained_snapshots = 1
    competing_runtime.workspace_snapshot_store.max_retained_snapshots = 1

    task = asyncio.create_task(runtime.run("Wait.", session_id="session-in-flight"))
    await asyncio.wait_for(started.wait(), timeout=1)
    pending_snapshot = runtime.list_workspace_snapshots()[0]

    competing_runtime.create_workspace_snapshot(label="newer-snapshot")

    assert pending_snapshot.snapshot_id in {
        snapshot.snapshot_id for snapshot in runtime.list_workspace_snapshots()
    }

    release.set()
    result = await asyncio.wait_for(task, timeout=1)

    assert result.status == LoopStatus.COMPLETED
    # Releasing the in-flight lease re-prunes, but the finalized revert target
    # is retained by reference, so it is still there for a later revert.
    assert (
        runtime.get_session("session-in-flight").metadata["revert"][
            "workspace_snapshot_id"
        ]
        == pending_snapshot.snapshot_id
    )
    assert pending_snapshot.snapshot_id in {
        snapshot.snapshot_id for snapshot in runtime.list_workspace_snapshots()
    }
    reverted = runtime.revert_session("session-in-flight")
    assert reverted.metadata["revert"]["status"] == "reverted"
    assert reverted.metadata["revert"]["workspace_restore_status"] == "restored"


@pytest.mark.asyncio
async def test_runtime_retention_preserves_active_unrevert_snapshot(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-write-active-unrevert",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {"filePath": "created.txt", "content": "created\n"},
                                sort_keys=True,
                            ),
                        },
                    }
                ]
            },
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )
    assert runtime.workspace_snapshot_store is not None
    runtime.workspace_snapshot_store.max_retained_snapshots = 2

    await runtime.run("Create a file.", session_id="session-active-unrevert")
    runtime.revert_session("session-active-unrevert")
    unrevert_snapshot_id = runtime.get_session("session-active-unrevert").metadata[
        "revert"
    ]["unrevert_snapshot_id"]
    runtime.store.get_session_summary = None
    runtime.create_workspace_snapshot(label="newer-one")
    runtime.create_workspace_snapshot(label="newer-two")

    assert unrevert_snapshot_id in {
        snapshot.snapshot_id for snapshot in runtime.list_workspace_snapshots()
    }
    unreverted = runtime.unrevert_session("session-active-unrevert")
    assert (tmp_path / "created.txt").read_bytes() == b"created\n"
    assert unreverted.metadata["revert"]["status"] == "unreverted"


@pytest.mark.asyncio
async def test_runtime_revert_restores_workspace_despite_retention_cap(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-write-retention-one",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {"filePath": "created.txt", "content": "created\n"},
                                sort_keys=True,
                            ),
                        },
                    }
                ]
            },
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )
    assert runtime.workspace_snapshot_store is not None
    runtime.workspace_snapshot_store.max_retained_snapshots = 1

    await runtime.run("Create a file.", session_id="session-retention-one")
    assert (tmp_path / "created.txt").exists()
    reverted = runtime.revert_session("session-retention-one")

    # Even with a cap of one snapshot the BEFORE target is retained, so the
    # revert actually undoes the turn's file changes instead of only trimming
    # history and reporting success.
    assert reverted.metadata["revert"]["workspace_snapshot_id"]
    assert reverted.metadata["revert"]["unrevert_snapshot_id"]
    assert reverted.metadata["revert"]["status"] == "reverted"
    assert reverted.metadata["revert"]["workspace_restored"] is True
    assert not (tmp_path / "created.txt").exists()

    runtime.unrevert_session("session-retention-one")
    assert (tmp_path / "created.txt").read_bytes() == b"created\n"
    reverted_again = runtime.revert_session("session-retention-one")
    assert reverted_again.metadata["revert"]["status"] == "reverted"
    assert not (tmp_path / "created.txt").exists()


def _writing_provider(call_id: str, path: str, content: str) -> ScriptedLLMProvider:
    return ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {"filePath": path, "content": content},
                                sort_keys=True,
                            ),
                        },
                    }
                ]
            },
            {"content": "done"},
        ]
    )


@pytest.mark.asyncio
async def test_revert_still_restores_after_many_newer_snapshots_in_workspace(
    tmp_path: Path,
):
    """Subagent/other-session snapshots must not evict a live revert target."""
    runtime = AgentRuntime(
        provider=_writing_provider("call-write-crowded", "created.txt", "created\n"),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )
    # A subagent ("task") run shares workspace_root and the session store (see
    # _default_task_runner) and inherits enable_session_revert_snapshots, so it
    # churns the same retention window.
    subagent_runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            model_aware_tool_selection=False,
        ),
        store=runtime.store,
    )
    assert runtime.workspace_snapshot_store is not None
    assert subagent_runtime.workspace_snapshot_store is not None

    await runtime.run("Create a file.", session_id="session-crowded")
    target_snapshot_id = runtime.get_session("session-crowded").metadata["revert"][
        "workspace_snapshot_id"
    ]
    assert target_snapshot_id
    assert (tmp_path / "created.txt").exists()

    cap = runtime.workspace_snapshot_store.max_retained_snapshots
    for index in range(cap + 5):
        subagent_runtime.create_workspace_snapshot(label=f"subagent-{index}")

    assert target_snapshot_id in {
        snapshot.snapshot_id for snapshot in runtime.list_workspace_snapshots()
    }
    reverted = runtime.revert_session("session-crowded")

    assert reverted.metadata["revert"]["status"] == "reverted"
    assert reverted.metadata["revert"]["workspace_restored"] is True
    assert not (tmp_path / "created.txt").exists()


@pytest.mark.asyncio
async def test_revert_without_its_workspace_snapshot_reports_history_only(
    tmp_path: Path,
):
    """A lost workspace target must not be reported as a complete revert."""
    runtime = AgentRuntime(
        provider=_writing_provider("call-write-lost", "created.txt", "created\n"),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )

    await runtime.run("Create a file.", session_id="session-lost-target")
    target_snapshot_id = runtime.get_session("session-lost-target").metadata["revert"][
        "workspace_snapshot_id"
    ]
    assert target_snapshot_id
    message_count = len(runtime.get_session("session-lost-target").messages)

    # Deleting the snapshot invalidates the session's reference, exactly as
    # pruning would.
    runtime.delete_workspace_snapshot(target_snapshot_id)
    assert (
        runtime.get_session("session-lost-target").metadata["revert"][
            "workspace_snapshot_id"
        ]
        is None
    )

    reverted = runtime.revert_session("session-lost-target")
    revert_metadata = reverted.metadata["revert"]

    assert revert_metadata["status"] == "reverted_history_only"
    assert revert_metadata["workspace_restore_status"] == "history_only"
    assert revert_metadata["workspace_restored"] is False
    # History was trimmed, but the turn's file change is still on disk.
    assert len(reverted.messages) < message_count
    assert (tmp_path / "created.txt").read_bytes() == b"created\n"


@pytest.mark.asyncio
async def test_revert_without_workspace_snapshots_enabled_stays_a_plain_revert(
    tmp_path: Path,
):
    """No workspace snapshots configured is not a partial revert."""
    runtime = AgentRuntime(
        provider=_writing_provider("call-write-no-ws", "created.txt", "created\n"),
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            enable_session_revert_snapshots=False,
            tool_permissions={"write": "allow"},
        ),
    )

    await runtime.run("Create a file.", session_id="session-no-workspace-snapshots")
    reverted = runtime.revert_session("session-no-workspace-snapshots")
    revert_metadata = reverted.metadata["revert"]

    assert revert_metadata["workspace_snapshot_id"] is None
    assert revert_metadata["status"] == "reverted"
    assert revert_metadata["workspace_restore_status"] == "not_applicable"
    assert revert_metadata["workspace_restored"] is False


@pytest.mark.asyncio
async def test_revert_reports_paths_the_snapshot_could_not_capture(tmp_path: Path):
    """A >max_file_bytes file and an excluded dir make the revert partial."""
    runtime = AgentRuntime(
        provider=_writing_provider("call-write-partial", "created.txt", "created\n"),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )
    assert runtime.workspace_snapshot_store is not None
    runtime.workspace_snapshot_store.max_file_bytes = 16

    big = tmp_path / "big.bin"
    big.write_text("x" * 64, encoding="utf-8")
    dependency = tmp_path / "node_modules" / "pkg.js"
    dependency.parent.mkdir(parents=True, exist_ok=True)
    dependency.write_text("dep\n", encoding="utf-8")

    await runtime.run("Create a file.", session_id="session-partial")
    # The turn also touched what the snapshot could not capture.
    big.write_text("y" * 64, encoding="utf-8")
    dependency.write_text("changed\n", encoding="utf-8")

    reverted = runtime.revert_session("session-partial")
    revert_metadata = reverted.metadata["revert"]

    assert revert_metadata["status"] == "reverted"
    assert revert_metadata["workspace_restore_status"] == "partial"
    assert revert_metadata["workspace_restored"] is True
    assert revert_metadata["workspace_unrestored_paths"] == ["big.bin"]
    assert "node_modules" in revert_metadata["workspace_excluded_directories"]
    # Reported precisely because they really were not put back.
    assert big.read_text(encoding="utf-8") == "y" * 64
    assert dependency.read_text(encoding="utf-8") == "changed\n"
    assert not (tmp_path / "created.txt").exists()


def _write_legacy_snapshot_on_disk(root: Path, snapshot_id: str) -> Path:
    """A retained snapshot written by the pre-file-map code (format 1)."""
    snapshot_dir = root / ".efp_runtime" / "workspace_snapshots" / snapshot_id
    blob = snapshot_dir / "files" / "captured.txt"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"captured\n")
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(
            {
                "snapshot_id": snapshot_id,
                "workspace_root": str(root),
                "created_at": "2026-01-01T00:00:00+00:00",
                "label": "legacy",
                "metadata": {},
                "file_count": 1,
                "total_bytes": len(b"captured\n"),
            }
        ),
        encoding="utf-8",
    )
    return snapshot_dir


class _SnapshotDirectoryWatch:
    """Records reads of the workspace snapshot storage directory."""

    def __init__(self, monkeypatch, root: Path) -> None:
        from efp_runtime import workspace_snapshots

        self.storage_root = (root / ".efp_runtime" / "workspace_snapshots").resolve()
        self.listed: list[Path] = []
        self.hashed: list[Path] = []

        original_iterdir = Path.iterdir
        original_stream_sha256 = workspace_snapshots._stream_sha256

        def tracked_iterdir(directory: Path):
            if Path(directory).resolve() == self.storage_root:
                self.listed.append(Path(directory))
            return original_iterdir(directory)

        def tracked_stream_sha256(path: Path):
            self.hashed.append(Path(path))
            return original_stream_sha256(path)

        monkeypatch.setattr(Path, "iterdir", tracked_iterdir)
        monkeypatch.setattr(
            workspace_snapshots, "_stream_sha256", tracked_stream_sha256
        )


@pytest.mark.asyncio
async def test_disabled_revert_snapshots_never_read_the_snapshot_directory(
    tmp_path: Path,
    monkeypatch,
):
    """A runtime that will not snapshot must not pay to load the store.

    ``AgentRuntime`` is constructed once per chat request and building the
    store walks every retained snapshot on the workspace volume, re-hashing
    legacy ones. With the feature off that is pure cost, so the store is only
    built when something actually asks for it.
    """
    _write_legacy_snapshot_on_disk(tmp_path, "workspace_snapshot_1")
    watch = _SnapshotDirectoryWatch(monkeypatch, tmp_path)

    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "done"}]),
        config=RuntimeConfig(
            enable_session_revert_snapshots=False,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
        ),
    )
    result = await runtime.run("hey", session_id="session-no-snapshots")

    assert result.status == LoopStatus.COMPLETED
    assert watch.listed == []
    assert watch.hashed == []
    assert runtime.get_session("session-no-snapshots").metadata["revert"][
        "workspace_snapshot_id"
    ] is None

    # The store is still fully available to anything that does ask for it.
    assert [
        snapshot.snapshot_id for snapshot in runtime.list_workspace_snapshots()
    ] == ["workspace_snapshot_1"]
    assert watch.listed


@pytest.mark.asyncio
async def test_enabled_revert_snapshots_still_capture_and_restore(
    tmp_path: Path,
    monkeypatch,
):
    """With the feature on, the deferred store is built and the revert works."""
    watch = _SnapshotDirectoryWatch(monkeypatch, tmp_path)
    runtime = AgentRuntime(
        provider=_writing_provider("call-write-lazy", "created.txt", "created\n"),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )

    await runtime.run("Create a file.", session_id="session-lazy-enabled")

    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created\n"
    assert runtime.get_session("session-lazy-enabled").metadata["revert"][
        "workspace_snapshot_id"
    ]
    assert watch.listed

    reverted = runtime.revert_session("session-lazy-enabled")

    assert not (tmp_path / "created.txt").exists()
    assert reverted.metadata["revert"]["workspace_restore_status"] == "restored"


@pytest.mark.asyncio
async def test_complete_revert_reports_no_unrestored_paths(tmp_path: Path):
    runtime = AgentRuntime(
        provider=_writing_provider("call-write-clean", "created.txt", "created\n"),
        config=RuntimeConfig(
            enable_session_revert_snapshots=True,
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )

    await runtime.run("Create a file.", session_id="session-clean")
    revert_metadata = runtime.revert_session("session-clean").metadata["revert"]

    assert revert_metadata["workspace_restore_status"] == "restored"
    assert revert_metadata["workspace_unrestored_paths"] == []
    assert revert_metadata["workspace_excluded_directories"] == []
