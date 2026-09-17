"""Tests for the session_search tool: cross-session memory over stored sessions."""

from __future__ import annotations

from pathlib import Path

import pytest

from efp_runtime import FileSessionStore, MessagePart
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.file_store import build_session_summary
from efp_runtime.session.models import CompactionPart
from efp_runtime.session.search import (
    SCOPE_AGENT,
    SCOPE_MINE,
    is_task_session_id,
    parse_query,
    recent_sessions_overview,
    search_transcripts,
    select_candidates,
    transcript_from_messages,
)
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.system_prompt import SystemPromptBuilder
from efp_runtime.tools.builtin import (
    SESSION_SEARCH_TOOL_ID,
    create_core_tool_registry,
    create_session_search_tool,
)
from efp_runtime.tools.builtin.session_search import TranscriptCache
from efp_runtime.tools.definition import ToolContext
from efp_runtime.types import ToolCall, ToolResult


ALICE = {"author_id": "alice-id", "author_name": "Alice"}
BOB = {"author_id": "bob-id", "author_name": "Bob"}


def _member(text: str, author: dict | None = ALICE, *, original: str | None = None) -> tuple:
    metadata = {"source": "loop.user", "author_type": "human"}
    if author:
        metadata.update(author)
    if original is not None:
        metadata["original_user_message"] = original
    return ("user", [MessagePart.text_part(text)], metadata)


def _assistant(text: str) -> tuple:
    return ("assistant", [MessagePart.text_part(text)], {})


def _seed(store, session_id: str, turns, *, custom_name: str | None = None, title: str | None = None):
    metadata = {"custom_session_name": custom_name} if custom_name else None
    store.create_session(session_id=session_id, title=title, metadata=metadata)
    for role, parts, metadata in turns:
        store.append_message(
            session_id,
            role=role,
            parts=parts,
            metadata=metadata,
            status="complete",
        )


def _seed_tool_pair(store, session_id: str, *, output: str) -> None:
    call = ToolCall(tool_name="bash", arguments={"command": "grep timeout"}, call_id="call-1")
    store.append_message(
        session_id,
        role="assistant",
        parts=[MessagePart.tool_call_part(call)],
        status="complete",
    )
    result = ToolResult(
        call_id="call-1",
        tool_name="bash",
        content=output,
        output=output,
        success=True,
        status="success",
    )
    store.append_message(
        session_id,
        role="tool",
        parts=[MessagePart.tool_result_part(result)],
        status="complete",
    )


def _seed_world(store) -> None:
    _seed(
        store,
        "chat-alice-timeout",
        [
            _member("The login timeout on PAY-123 keeps failing for customers."),
            _assistant("I raised the nginx proxy timeout to 60s and redeployed the gateway."),
        ],
        custom_name="Login timeout fix",
    )
    _seed_tool_pair(store, "chat-alice-timeout", output="SECRET-TOOL-OUTPUT timeout timeout timeout")
    _seed(
        store,
        "chat-alice-release",
        [
            _member("Prepare the release notes for 2.4."),
            _assistant("Drafted release notes covering the payments changes."),
        ],
    )
    _seed(
        store,
        "chat-bob-timeout",
        [
            _member("Bob here: the checkout timeout is back on staging.", BOB),
            _assistant("Checkout timeout comes from the payment provider."),
        ],
    )
    _seed(
        store,
        "task-nightly-timeout",
        [
            _member("Nightly task: scan the logs for timeout errors.", None),
            _assistant("Found 3 timeout errors in the nightly logs."),
        ],
    )
    _seed(
        store,
        "chat-current",
        [
            _member("Current session also mentions a timeout."),
        ],
    )


def _context(session_id: str = "chat-current", *, viewer: str | None = "alice-id") -> ToolContext:
    metadata = {"portal_user_id": viewer} if viewer else {}
    return ToolContext(session_id=session_id, metadata=metadata, tool_call_id="call-search")


async def _run(tool, args: dict, context: ToolContext):
    return await tool.execute(tool.validate_args(args), context)


@pytest.mark.asyncio
async def test_search_scopes_to_member_and_never_quotes_tool_output(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed_world(store)
    tool = create_session_search_tool(store)

    result = await _run(tool, {"query": "timeout"}, _context())

    assert result.success is True
    session_ids = [item["session_id"] for item in result.output["results"]]
    assert session_ids == ["chat-alice-timeout"]
    assert result.output["scope"] == SCOPE_MINE
    assert "SECRET-TOOL-OUTPUT" not in result.content
    assert "Login timeout fix" in result.content
    assert "session_id: chat-alice-timeout" in result.content
    assert "member Alice" in result.content
    assert "login timeout on PAY-123" in result.content
    assert "chat-current" not in result.content
    assert result.metadata["session_ids"] == ["chat-alice-timeout"]


@pytest.mark.asyncio
async def test_agent_scope_includes_other_members_and_task_sessions(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed_world(store)
    tool = create_session_search_tool(store)

    mine = await _run(tool, {"query": "timeout", "scope": "mine"}, _context())
    everything = await _run(tool, {"query": "timeout", "scope": "agent"}, _context())

    assert [item["session_id"] for item in mine.output["results"]] == ["chat-alice-timeout"]
    agent_ids = {item["session_id"] for item in everything.output["results"]}
    assert agent_ids == {"chat-alice-timeout", "chat-bob-timeout", "task-nightly-timeout"}
    assert "chat-current" not in agent_ids
    assert everything.output["scope"] == SCOPE_AGENT


@pytest.mark.asyncio
async def test_search_without_member_identity_fails_closed_and_says_so(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed_world(store)
    tool = create_session_search_tool(store)

    result = await _run(tool, {"query": "timeout"}, _context(viewer=None))
    explicit = await _run(tool, {"query": "timeout", "scope": "agent"}, _context(viewer=None))

    assert result.success is True
    assert result.output["scope"] == SCOPE_MINE
    assert result.output["results"] == []
    assert result.output["sessions_in_scope"] == 0
    assert "No member identity" in result.content
    assert "No session is in scope" in result.content
    assert {item["session_id"] for item in explicit.output["results"]} >= {
        "chat-alice-timeout",
        "chat-bob-timeout",
    }


@pytest.mark.asyncio
async def test_identity_comes_from_header_derived_portal_user_id_only(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed_world(store)
    tool = create_session_search_tool(store)
    spoofed = ToolContext(
        session_id="chat-current",
        metadata={"portal_user": {"id": "bob-id", "display_name": "Bob"}},
        tool_call_id="call-search",
    )

    result = await _run(tool, {"query": "timeout"}, spoofed)

    assert result.output["results"] == []
    assert "No member identity" in result.content


@pytest.mark.asyncio
async def test_read_mode_requires_agent_scope_for_other_members_sessions(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed_world(store)
    tool = create_session_search_tool(store)

    own = await _run(tool, {"session_id": "chat-alice-timeout"}, _context())
    other = await _run(tool, {"session_id": "chat-bob-timeout"}, _context())
    other_explicit = await _run(tool, {"session_id": "chat-bob-timeout", "scope": "agent"}, _context())
    anonymous = await _run(tool, {"session_id": "chat-alice-timeout"}, _context(viewer=None))
    anonymous_explicit = await _run(
        tool, {"session_id": "chat-alice-timeout", "scope": "agent"}, _context(viewer=None)
    )
    # The schema already rejects unknown scopes; the tool checks again so a
    # caller that skips validation cannot slip past the boundary.
    bad_scope = await tool.execute({"query": "timeout", "scope": "everyone"}, _context())

    assert own.success is True
    assert other.success is False and 'scope="agent"' in other.content
    assert other_explicit.success is True and "Bob" in other_explicit.content
    assert anonymous.success is False and "member identity" in anonymous.content
    assert anonymous_explicit.success is True
    assert bad_scope.success is False and "Unknown scope" in bad_scope.content


@pytest.mark.asyncio
async def test_search_ranks_phrase_matches_first_and_reports_no_match(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed_world(store)
    tool = create_session_search_tool(store)

    ranked = await _run(tool, {"query": "checkout timeout", "scope": "agent"}, _context())
    nothing = await _run(tool, {"query": "kubernetes"}, _context())

    assert ranked.output["results"][0]["session_id"] == "chat-bob-timeout"
    assert nothing.success is True
    assert nothing.output["results"] == []
    assert "No earlier session mentions these keywords" in nothing.content


@pytest.mark.asyncio
async def test_read_mode_pages_turns_and_shows_member_words(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed(
        store,
        "chat-read",
        [
            _member(
                "Attachment context: <file contents> Question: what did we decide?",
                original="what did we decide?",
            ),
            _assistant("We decided to keep the monolith."),
            _member("Why?"),
            _assistant("Because splitting it would take two quarters."),
        ],
        custom_name="Monolith decision",
    )
    tool = create_session_search_tool(store)

    first = await _run(tool, {"session_id": "chat-read", "max_turns": 2}, _context())
    second = await _run(tool, {"session_id": "chat-read", "turn_offset": 2, "max_turns": 2}, _context())

    assert first.success is True
    assert first.output["total_turns"] == 4
    assert first.output["next_turn_offset"] == 2
    assert [turn["text"] for turn in first.output["turns"]] == [
        "what did we decide?",
        "We decided to keep the monolith.",
    ]
    assert "<file contents>" not in first.content
    assert "Monolith decision" in first.content
    assert "Turns 1-2 of 4" in first.content
    assert "turn_offset=2" in first.content
    assert second.output["next_turn_offset"] is None
    assert [turn["turn"] for turn in second.output["turns"]] == [3, 4]
    assert "Because splitting it" in second.content


@pytest.mark.asyncio
async def test_read_mode_rejects_current_and_unknown_sessions(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed_world(store)
    tool = create_session_search_tool(store)

    current = await _run(tool, {"session_id": "chat-current"}, _context())
    unknown = await _run(tool, {"session_id": "chat-missing"}, _context())
    neither = await _run(tool, {}, _context())

    assert current.success is False
    assert "current session" in current.content
    assert unknown.success is False
    assert "Unknown session_id" in unknown.content
    assert neither.success is False
    assert "`query`" in neither.content


@pytest.mark.asyncio
async def test_cjk_query_matches_substrings(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed(
        store,
        "chat-cjk",
        [
            _member("昨天登录超时的问题还没有解决，PAY-123 还在等。"),
            _assistant("我把网关超时改成了 60 秒。"),
        ],
    )
    _seed(store, "chat-other", [_member("完全无关的话题。")])
    tool = create_session_search_tool(store)

    result = await _run(tool, {"query": "登录超时"}, _context(session_id="chat-now"))

    assert [item["session_id"] for item in result.output["results"]] == ["chat-cjk"]
    assert "登录超时" in result.content


def test_parse_query_splits_latin_words_and_cjk_runs():
    terms, phrase = parse_query("Login Timeout 登录超时 x")

    texts = {term.text: term.weight for term in terms}
    assert phrase == "login timeout 登录超时 x"
    assert texts["login"] == 1.0
    assert texts["timeout"] == 1.0
    assert texts["登录超时"] == 1.0
    assert texts["登录"] == pytest.approx(0.3)
    assert "x" not in texts


def test_transcript_skips_tool_parts_and_synthetic_user_turns(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed(
        store,
        "chat-transcript",
        [
            _member("Real member words."),
            ("user", [MessagePart.text_part("Injected task result.")], {"source": "background_task.injected"}),
            _assistant("Assistant words."),
            (
                "system",
                [
                    MessagePart.compaction_part(
                        CompactionPart(summary="## Goal\n- keep the monolith")
                    )
                ],
                {},
            ),
        ],
    )
    _seed_tool_pair(store, "chat-transcript", output="tool noise")

    summary = build_session_summary(store.get_session("chat-transcript"))
    transcript = transcript_from_messages(summary, store.read_history("chat-transcript"))

    assert [(turn.role, turn.text) for turn in transcript.turns] == [
        ("member", "Real member words."),
        ("assistant", "Assistant words."),
        ("summary", "## Goal\n- keep the monolith"),
    ]
    assert transcript.author_ids == ("alice-id",)
    assert transcript.author_names == ("Alice",)
    assert summary.author_ids == ("alice-id",)
    assert summary.author_names == ("Alice",)


def test_select_candidates_orders_newest_first_and_filters_scope():
    store = InMemorySessionStore()
    _seed(store, "old", [_member("old words")])
    _seed(store, "task-x", [_member("task words", None)])
    _seed(store, "bobs", [_member("bob words", BOB)])
    _seed(store, "new", [_member("new words")])
    _seed(store, "empty", [])
    summaries = store.list_session_summaries()

    mine = select_candidates(summaries, exclude_session_id="new", viewer_id="alice-id")
    everything = select_candidates(
        summaries,
        exclude_session_id=None,
        viewer_id="alice-id",
        scope=SCOPE_AGENT,
        include_task_sessions=True,
    )
    anonymous = select_candidates(summaries, exclude_session_id=None, viewer_id=None)

    assert [item.session_id for item in mine.summaries] == ["old"]
    assert mine.scope == SCOPE_MINE and mine.scope_note is None
    assert {item.session_id for item in everything.summaries} == {"old", "task-x", "bobs", "new"}
    assert everything.summaries[0].session_id == "new"
    assert anonymous.scope == SCOPE_MINE and anonymous.summaries == ()
    assert anonymous.total_in_scope == 0 and anonymous.scope_note
    assert is_task_session_id("task-x") and not is_task_session_id("chat-x")
    with pytest.raises(ValueError):
        select_candidates(summaries, exclude_session_id=None, viewer_id=None, scope="everyone")


def test_search_transcripts_bounds_excerpts_and_limit():
    store = InMemorySessionStore()
    for index in range(4):
        _seed(
            store,
            f"chat-{index}",
            [_member("filler " * 80 + f"needle-{index} " + "tail " * 80)],
            custom_name=f"Session {index}",
        )
    transcripts = [
        transcript_from_messages(summary, store.read_history(summary.session_id))
        for summary in store.list_session_summaries()
    ]

    matches = search_transcripts(transcripts, "needle-2 needle-3", limit=1)

    assert len(matches) == 1
    excerpt = matches[0].excerpts[0].text
    assert excerpt.startswith("…") and excerpt.endswith("…")
    assert len(excerpt) < 400
    assert "needle-" in excerpt


def test_transcript_cache_reuses_unchanged_sessions_and_evicts_old_ones():
    store = InMemorySessionStore()
    _seed(store, "chat-a", [_member("a")])
    _seed(store, "chat-b", [_member("b")])
    cache = TranscriptCache(max_entries=1)
    loads: list[str] = []

    def loader(summary):
        loads.append(summary.session_id)
        return transcript_from_messages(summary, store.read_history(summary.session_id))

    summary_a = store.get_session_summary("chat-a")
    cache.get(summary_a, loader)
    cache.get(summary_a, loader)
    assert loads == ["chat-a"]

    store.append_message("chat-a", role="user", parts=[MessagePart.text_part("more")], status="complete")
    changed = store.get_session_summary("chat-a")
    assert changed.message_count == 2
    cache.get(changed, loader)
    assert loads == ["chat-a", "chat-a"]

    cache.get(store.get_session_summary("chat-b"), loader)
    assert len(cache) == 1
    cache.get(changed, loader)
    assert loads == ["chat-a", "chat-a", "chat-b", "chat-a"]


def test_transcript_cache_evicts_by_char_budget_but_keeps_newest():
    store = InMemorySessionStore()
    for index in range(3):
        _seed(store, f"chat-{index}", [_member("x" * 1000)])
    cache = TranscriptCache(max_entries=10, max_chars=1500)

    def loader(summary):
        return transcript_from_messages(summary, store.read_history(summary.session_id))

    for index in range(3):
        cache.get(store.get_session_summary(f"chat-{index}"), loader)

    assert len(cache) == 1
    assert cache.total_chars <= 1500 or len(cache) == 1
    loads: list[str] = []

    def counting(summary):
        loads.append(summary.session_id)
        return loader(summary)

    cache.get(store.get_session_summary("chat-2"), counting)
    assert loads == []


def test_shared_transcript_cache_is_per_session_root(tmp_path: Path):
    file_store = FileSessionStore(tmp_path / "a")
    other_file_store = FileSessionStore(tmp_path / "b")
    memory_store = InMemorySessionStore()

    first = create_session_search_tool(file_store).runtime_metadata["transcript_cache"]
    second = create_session_search_tool(file_store).runtime_metadata["transcript_cache"]
    other = create_session_search_tool(other_file_store).runtime_metadata["transcript_cache"]
    private_a = create_session_search_tool(memory_store).runtime_metadata["transcript_cache"]
    private_b = create_session_search_tool(memory_store).runtime_metadata["transcript_cache"]

    assert first is second
    assert first is not other
    assert private_a is not private_b


def test_summary_list_cache_reuses_listing_within_ttl(tmp_path: Path):
    from efp_runtime.session.search import SessionSummaryListCache

    store = FileSessionStore(tmp_path)
    _seed(store, "chat-a", [_member("a")])
    now = [1000.0]
    cache = SessionSummaryListCache(ttl_seconds=30.0, clock=lambda: now[0])
    calls: list[int] = []
    original = store.list_session_summaries

    def counting():
        calls.append(1)
        return original()

    store.list_session_summaries = counting

    first = cache.get(store)
    _seed(store, "chat-b", [_member("b")])
    second = cache.get(store)
    now[0] += 31.0
    third = cache.get(store)

    assert [item.session_id for item in first] == ["chat-a"]
    assert [item.session_id for item in second] == ["chat-a"]
    assert sorted(item.session_id for item in third) == ["chat-a", "chat-b"]
    assert len(calls) == 2
    assert cache.get(InMemorySessionStore()) == []


def test_recent_sessions_overview_lists_member_sessions_newest_first():
    store = InMemorySessionStore()
    _seed(store, "older", [_member("older words")], custom_name="Older chat")
    _seed(store, "bobs", [_member("bob words", BOB)])
    _seed(store, "newer", [_member("newer words")], custom_name="Newer chat")
    _seed(store, "current", [_member("current words")])

    overview = recent_sessions_overview(
        store.list_session_summaries(),
        exclude_session_id="current",
        viewer_id="alice-id",
        limit=5,
    )

    assert overview["scope"] == SCOPE_MINE
    assert overview["total_in_scope"] == 2
    assert [item["name"] for item in overview["sessions"]] == ["Newer chat", "Older chat"]
    assert overview["sessions"][0]["authors"] == ["Alice"]
    assert overview["sessions"][0]["member_turns"] == 1


def test_system_prompt_builder_renders_session_memory_block():
    builder = SystemPromptBuilder(workspace_root=None)
    metadata = {
        "session_memory": {
            "tool_id": "session_search",
            "scope": "mine",
            "total_in_scope": 3,
            "sessions": [
                {
                    "session_id": "chat-1",
                    "name": "Login timeout fix",
                    "updated_at": "2026-09-15T10:22:33Z",
                    "member_turns": 4,
                    "authors": ["Alice"],
                },
            ],
        }
    }

    messages = builder.build_messages(metadata)

    assert len(messages) == 1
    text = messages[0].parts[0].text
    assert text.startswith("Earlier sessions:")
    assert "`session_search`" in text
    assert "Recent earlier sessions of this member" in text
    assert "3 in total" in text
    assert '- 2026-09-15 10:22 · "Login timeout fix" · session_id: chat-1 · 4 member turn(s) · Alice' in text
    assert messages[0].metadata["source"] == "session_memory_context"
    assert messages[0].metadata["session_count"] == 1
    assert builder.build_messages({}) == []


def test_system_prompt_builder_renders_empty_session_memory_with_note():
    builder = SystemPromptBuilder(workspace_root=None)
    messages = builder.build_messages(
        {
            "session_memory": {
                "scope": "agent",
                "scope_note": "No member identity was attached to this run.",
                "sessions": [],
            }
        }
    )

    text = messages[0].parts[0].text
    assert "of this assistant" in text
    assert "- (none yet)" in text
    assert text.endswith("Note: No member identity was attached to this run.")


def test_registry_registers_session_search_only_when_requested(tmp_path: Path):
    store = InMemorySessionStore()

    default_registry = create_core_tool_registry(tmp_path)
    enabled_registry = create_core_tool_registry(
        tmp_path,
        include_session_search_tool=True,
        session_store=store,
    )

    assert default_registry.get(SESSION_SEARCH_TOOL_ID) is None
    tool = enabled_registry.require(SESSION_SEARCH_TOOL_ID)
    assert tool.runtime_metadata["session_store"] is store
    assert tool.permission.action == "allow"
    assert tool.permission.category == "memory"
    assert "additionalProperties" in tool.input_schema


def test_runtime_config_coerces_enable_session_search(tmp_path: Path):
    assert RuntimeConfig(workspace_root=tmp_path).enable_session_search is False
    assert RuntimeConfig(workspace_root=tmp_path, enable_session_search=1).enable_session_search is True


@pytest.mark.asyncio
async def test_agent_runtime_offers_tool_and_lists_earlier_sessions(tmp_path: Path):
    sessions_root = tmp_path / "sessions"
    store = FileSessionStore(sessions_root)
    _seed(
        store,
        "chat-earlier",
        [
            _member("Please fix the login timeout."),
            _assistant("Fixed."),
        ],
        custom_name="Login timeout fix",
    )
    _seed(store, "chat-bob", [_member("Bob's own chat.", BOB)], custom_name="Bob chat")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        store=store,
        config=RuntimeConfig(
            workspace_root=workspace,
            enable_session_search=True,
            max_iterations=2,
        ),
    )

    result = await runtime.run(
        "What did we do last time?",
        session_id="chat-now",
        metadata={"portal_user_id": "alice-id", "portal_user_name": "Alice"},
    )

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert SESSION_SEARCH_TOOL_ID in [schema.id for schema in request.provider_request.tools]
    system_texts = [
        message.text for message in request.provider_request.messages if message.role == "system"
    ]
    memory_blocks = [text for text in system_texts if text.startswith("Earlier sessions:")]
    assert len(memory_blocks) == 1
    assert "Login timeout fix" in memory_blocks[0]
    assert "session_id: chat-earlier" in memory_blocks[0]
    assert "Bob chat" not in memory_blocks[0]
    assert "chat-now" not in memory_blocks[0]
    assert request.metadata["session_memory"]["scope"] == SCOPE_MINE


@pytest.mark.asyncio
async def test_agent_runtime_without_identity_lists_nothing_but_explains(tmp_path: Path):
    store = FileSessionStore(tmp_path / "sessions")
    _seed(store, "chat-earlier", [_member("Earlier words.")], custom_name="Earlier chat")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        store=store,
        config=RuntimeConfig(workspace_root=workspace, enable_session_search=True, max_iterations=2),
    )

    result = await runtime.run(
        "Hello",
        session_id="chat-now",
        metadata={"portal_user": {"id": "alice-id"}},
    )

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    memory = request.metadata["session_memory"]
    assert memory["sessions"] == []
    assert memory["scope"] == SCOPE_MINE
    assert "No member identity" in memory["scope_note"]
    block = next(
        message.text
        for message in request.provider_request.messages
        if message.role == "system" and message.text.startswith("Earlier sessions:")
    )
    assert "Earlier chat" not in block
    assert "No member identity" in block


@pytest.mark.asyncio
async def test_agent_runtime_hides_prompt_block_when_tool_is_not_offered(tmp_path: Path):
    store = FileSessionStore(tmp_path / "sessions")
    _seed(store, "chat-earlier", [_member("Earlier words.")], custom_name="Earlier chat")

    async def run_with(config: RuntimeConfig) -> list[str]:
        provider = ScriptedLLMProvider([{"content": "Done."}])
        runtime = AgentRuntime(provider=provider, store=store, config=config)
        result = await runtime.run(
            "Hello",
            session_id=f"chat-now-{id(config)}",
            metadata={"portal_user_id": "alice-id"},
        )
        assert result.status == LoopStatus.COMPLETED
        request = provider.requests[0]
        assert "session_memory" not in request.metadata
        return [
            message.text
            for message in request.provider_request.messages
            if message.role == "system"
        ]

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    disabled = await run_with(RuntimeConfig(workspace_root=workspace, max_iterations=2))
    allowlisted = await run_with(
        RuntimeConfig(
            workspace_root=workspace,
            enable_session_search=True,
            enabled_tools=["read"],
            max_iterations=2,
        )
    )
    denied = await run_with(
        RuntimeConfig(
            workspace_root=workspace,
            enable_session_search=True,
            disabled_tools=[SESSION_SEARCH_TOOL_ID],
            max_iterations=2,
        )
    )

    for texts in (disabled, allowlisted, denied):
        assert not any(text.startswith("Earlier sessions:") for text in texts)
