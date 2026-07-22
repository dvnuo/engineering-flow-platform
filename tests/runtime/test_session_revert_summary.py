from __future__ import annotations

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
async def test_runtime_pruning_invalidates_expired_session_snapshot_reference(
    tmp_path: Path,
):
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider(
            [{"content": "first"}, {"content": "second"}, {"content": "third"}]
        ),
        config=RuntimeConfig(
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
    runtime.store.list_sessions = lambda: pytest.fail(
        "snapshot pruning must not load every session body"
    )
    await runtime.run("Third", session_id="session-third")

    assert first_snapshot_id not in {
        snapshot.snapshot_id for snapshot in runtime.list_workspace_snapshots()
    }
    assert (
        runtime.get_session("session-first").metadata["revert"][
            "workspace_snapshot_id"
        ]
        is None
    )
    reverted = runtime.revert_session("session-first")
    assert reverted.metadata["revert"]["status"] == "reverted"


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
async def test_runtime_revert_does_not_republish_pruned_target(tmp_path: Path):
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
            workspace_root=tmp_path,
            max_iterations=2,
            model_aware_tool_selection=False,
            tool_permissions={"write": "allow"},
        ),
    )
    assert runtime.workspace_snapshot_store is not None
    runtime.workspace_snapshot_store.max_retained_snapshots = 1

    await runtime.run("Create a file.", session_id="session-retention-one")
    reverted = runtime.revert_session("session-retention-one")

    assert reverted.metadata["revert"]["workspace_snapshot_id"] is None
    assert reverted.metadata["revert"]["unrevert_snapshot_id"]
    runtime.unrevert_session("session-retention-one")
    assert (tmp_path / "created.txt").read_bytes() == b"created\n"
    reverted_again = runtime.revert_session("session-retention-one")
    assert reverted_again.metadata["revert"]["status"] == "reverted"
