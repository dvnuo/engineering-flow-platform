"""Tests for the memory tool: per-member standing notes across sessions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from efp_runtime import FileSessionStore
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.member_memory import (
    FileMemberNotesStore,
    InMemoryMemberNotesStore,
    MemberMemoryError,
)
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.system_prompt import SystemPromptBuilder
from efp_runtime.tools.builtin import (
    MEMORY_TOOL_ID,
    create_core_tool_registry,
    create_memory_tool,
)
from efp_runtime.tools.definition import ToolContext


def _context(viewer: str | None = "alice-id") -> ToolContext:
    metadata = {"portal_user_id": viewer} if viewer else {}
    return ToolContext(session_id="chat-now", metadata=metadata, tool_call_id="call-memory")


async def _run(tool, args: dict, context: ToolContext | None = None):
    return await tool.execute(tool.validate_args(args), context or _context())


def test_file_store_persists_notes_and_dedupes(tmp_path: Path):
    store = FileMemberNotesStore(tmp_path / "memory")

    note, created = store.add_note("alice-id", "  Reply   in Chinese. ")
    again, created_again = store.add_note("alice-id", "reply in chinese.")

    assert created is True and created_again is False
    assert note.text == "Reply in Chinese."
    assert again.note_id == note.note_id
    reloaded = FileMemberNotesStore(tmp_path / "memory")
    assert [item.text for item in reloaded.list_notes("alice-id")] == ["Reply in Chinese."]
    assert reloaded.list_notes("bob-id") == []
    payload = json.loads((tmp_path / "memory" / "alice-id.json").read_text(encoding="utf-8"))
    assert payload["member_id"] == "alice-id"
    assert payload["schema_version"] == 1
    assert payload["notes"][0]["note_id"] == note.note_id


def test_file_store_limits_and_validates(tmp_path: Path):
    store = FileMemberNotesStore(tmp_path, max_notes=2, max_note_chars=20)
    store.add_note("alice-id", "one")
    store.add_note("alice-id", "two")

    with pytest.raises(MemberMemoryError, match="maximum"):
        store.add_note("alice-id", "three")
    with pytest.raises(MemberMemoryError, match="at most 20"):
        store.add_note("bob-id", "x" * 21)
    with pytest.raises(MemberMemoryError, match="needs text"):
        store.add_note("bob-id", "   ")
    with pytest.raises(MemberMemoryError, match="member identity"):
        store.add_note("", "anything")

    first_id = store.list_notes("alice-id")[0].note_id
    removed = store.remove_note("alice-id", first_id)
    assert removed is not None and removed.text == "one"
    assert store.remove_note("alice-id", "n_missing") is None
    assert [item.text for item in store.list_notes("alice-id")] == ["two"]


def test_file_store_uses_safe_stable_file_names(tmp_path: Path):
    store = FileMemberNotesStore(tmp_path)

    plain = store.member_path("8f1c2d3e-uuid")
    odd = store.member_path("../../etc/passwd")

    assert plain == (tmp_path / "8f1c2d3e-uuid.json").resolve()
    assert odd.parent == tmp_path.resolve()
    assert odd.name.endswith(".json")
    assert ".." not in odd.name and "/" not in odd.name
    assert store.member_path("../../etc/passwd") == odd
    assert store.member_path("a/b") != store.member_path("a-b")


def test_file_store_ignores_corrupt_files(tmp_path: Path):
    store = FileMemberNotesStore(tmp_path)
    store.member_path("alice-id").write_text("{not json", encoding="utf-8")

    assert store.list_notes("alice-id") == []
    _, created = store.add_note("alice-id", "fresh start")
    assert created is True
    assert [item.text for item in store.list_notes("alice-id")] == ["fresh start"]


@pytest.mark.asyncio
async def test_memory_tool_remember_list_forget():
    store = InMemoryMemberNotesStore()
    tool = create_memory_tool(store)

    remembered = await _run(tool, {"action": "remember", "text": "Reply in Chinese."})
    duplicate = await _run(tool, {"action": "remember", "text": "reply in chinese."})
    listed = await _run(tool, {"action": "list"})
    note_id = remembered.output["note"]["note_id"]
    forgotten = await _run(tool, {"action": "forget", "note_id": note_id})
    empty = await _run(tool, {"action": "list"})

    assert remembered.success is True
    assert remembered.output["created"] is True
    assert "Remembered for this member" in remembered.content
    assert "Reply in Chinese." in remembered.content
    assert remembered.metadata["note_id"] == note_id
    assert duplicate.output["created"] is False
    assert "Already remembered" in duplicate.content
    assert listed.output["note_count"] == 1
    assert f"[{note_id}]" in listed.content
    assert forgotten.success is True
    assert "Forgot note" in forgotten.content
    assert empty.content == "No notes for this member yet."


@pytest.mark.asyncio
async def test_memory_tool_errors_are_explicit():
    store = InMemoryMemberNotesStore(max_notes=1)
    tool = create_memory_tool(store)
    await _run(tool, {"action": "remember", "text": "first"})

    no_identity = await _run(tool, {"action": "list"}, _context(viewer=None))
    full = await _run(tool, {"action": "remember", "text": "second"})
    missing_id = await _run(tool, {"action": "forget"})
    unknown_id = await _run(tool, {"action": "forget", "note_id": "n_nope"})
    empty_text = await _run(tool, {"action": "remember", "text": " "})

    assert no_identity.success is False and "member identity" in no_identity.content
    assert full.success is False and "maximum" in full.content
    assert missing_id.success is False and "note_id" in missing_id.content
    assert unknown_id.success is False and "No note with id n_nope" in unknown_id.content
    assert empty_text.success is False and "needs text" in empty_text.content
    assert [note.text for note in store.list_notes("alice-id")] == ["first"]


@pytest.mark.asyncio
async def test_memory_tool_keeps_members_apart():
    store = InMemoryMemberNotesStore()
    tool = create_memory_tool(store)
    await _run(tool, {"action": "remember", "text": "Alice likes tabs."}, _context("alice-id"))
    await _run(tool, {"action": "remember", "text": "Bob likes spaces."}, _context("bob-id"))

    alice = await _run(tool, {"action": "list"}, _context("alice-id"))
    bob = await _run(tool, {"action": "list"}, _context("bob-id"))

    assert [note["text"] for note in alice.output["notes"]] == ["Alice likes tabs."]
    assert [note["text"] for note in bob.output["notes"]] == ["Bob likes spaces."]


def test_system_prompt_builder_renders_member_notes():
    builder = SystemPromptBuilder(workspace_root=None)

    with_notes = builder.build_messages(
        {
            "member_memory": {
                "tool_id": "memory",
                "max_notes": 50,
                "notes": [
                    {
                        "note_id": "n_ab12cd34",
                        "text": "Reply in Chinese.",
                        "created_at": "2026-09-17T10:00:00Z",
                    }
                ],
            }
        }
    )
    without = builder.build_messages({"member_memory": {"tool_id": "memory", "max_notes": 50, "notes": []}})

    text = with_notes[0].parts[0].text
    assert text.startswith("Member notes:")
    assert "`memory`" in text
    assert "Notes (newest first, 1 of 50):" in text
    assert "- [n_ab12cd34] 2026-09-17: Reply in Chinese." in text
    assert with_notes[0].metadata["source"] == "member_memory_context"
    assert with_notes[0].metadata["note_count"] == 1
    assert "- (none yet)" in without[0].parts[0].text
    assert builder.build_messages({}) == []


def test_registry_registers_memory_only_when_requested(tmp_path: Path):
    store = InMemoryMemberNotesStore()

    default_registry = create_core_tool_registry(tmp_path)
    enabled_registry = create_core_tool_registry(
        tmp_path,
        include_memory_tool=True,
        member_notes_store=store,
    )

    assert default_registry.get(MEMORY_TOOL_ID) is None
    tool = enabled_registry.require(MEMORY_TOOL_ID)
    assert tool.runtime_metadata["member_notes_store"] is store
    assert tool.permission.action == "allow"
    assert tool.permission.category == "memory"


def test_runtime_config_coerces_member_memory_flags(tmp_path: Path):
    default = RuntimeConfig(workspace_root=tmp_path)
    configured = RuntimeConfig(
        workspace_root=tmp_path,
        enable_member_memory=1,
        member_memory_dir=tmp_path / "notes",
    )

    assert default.enable_member_memory is False
    assert default.member_memory_dir is None
    assert configured.enable_member_memory is True
    assert configured.member_memory_dir == tmp_path / "notes"


@pytest.mark.asyncio
async def test_agent_runtime_offers_memory_and_lists_notes_next_to_sessions(tmp_path: Path):
    sessions_root = tmp_path / "runtime"
    store = FileSessionStore(sessions_root)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    FileMemberNotesStore(sessions_root / "memory").add_note("alice-id", "Reply in Chinese.")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        store=store,
        config=RuntimeConfig(
            workspace_root=workspace,
            enable_member_memory=True,
            max_iterations=2,
        ),
    )

    result = await runtime.run(
        "Hello",
        session_id="chat-now",
        metadata={"portal_user_id": "alice-id", "portal_user_name": "Alice"},
    )

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert MEMORY_TOOL_ID in [schema.id for schema in request.provider_request.tools]
    blocks = [
        message.text
        for message in request.provider_request.messages
        if message.role == "system" and message.text.startswith("Member notes:")
    ]
    assert len(blocks) == 1
    assert "Reply in Chinese." in blocks[0]
    assert request.metadata["member_memory"]["note_count"] == 1
    tool = runtime.tool_runtime.registry.require(MEMORY_TOOL_ID)
    notes_store = tool.runtime_metadata["member_notes_store"]
    assert isinstance(notes_store, FileMemberNotesStore)
    assert notes_store.root == (sessions_root / "memory").resolve()


@pytest.mark.asyncio
async def test_agent_runtime_hides_member_notes_without_identity_or_tool(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async def system_texts(config: RuntimeConfig, metadata: dict) -> list[str]:
        provider = ScriptedLLMProvider([{"content": "Done."}])
        runtime = AgentRuntime(provider=provider, store=InMemorySessionStore(), config=config)
        result = await runtime.run("Hello", session_id="chat-now", metadata=metadata)
        assert result.status == LoopStatus.COMPLETED
        request = provider.requests[0]
        assert "member_memory" not in request.metadata
        return [
            message.text
            for message in request.provider_request.messages
            if message.role == "system"
        ]

    disabled = await system_texts(
        RuntimeConfig(workspace_root=workspace, max_iterations=2),
        {"portal_user_id": "alice-id"},
    )
    anonymous = await system_texts(
        RuntimeConfig(workspace_root=workspace, enable_member_memory=True, max_iterations=2),
        {},
    )
    allowlisted = await system_texts(
        RuntimeConfig(
            workspace_root=workspace,
            enable_member_memory=True,
            enabled_tools=["read"],
            max_iterations=2,
        ),
        {"portal_user_id": "alice-id"},
    )

    for texts in (disabled, anonymous, allowlisted):
        assert not any(text.startswith("Member notes:") for text in texts)
