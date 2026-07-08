"""Regression tests for FileSessionStore caching + summary listing.

These lock in the memory/CPU optimisation for the "many sessions + large
inlined tool output" case: repeated reads of an unchanged session must not
re-parse it, the list endpoint must answer from lightweight summaries without
loading full bodies, and none of the caching may change observable results
(stale reads, corrupted store state, or a different session display name).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import efp_runtime.session.file_store as fs_mod
from efp_runtime import FileSessionStore, MessagePart, MessageRole
from efp_runtime.session.file_store import SessionSummary, build_session_summary
from efp_runtime.session.gateway_facade import (
    RuntimeSessionManager,
    resolve_session_display_name,
)


def _seed(store: FileSessionStore, session_id: str, *, title=None, user_text="hi", assistant_text="ok"):
    store.create_session(session_id=session_id, title=title)
    store.append_message(
        session_id,
        role="user",
        parts=[MessagePart.text_part(user_text)],
        status="complete",
    )
    if assistant_text is not None:
        store.append_message(
            session_id,
            role="assistant",
            parts=[MessagePart.text_part(assistant_text)],
            status="complete",
        )


def test_summaries_match_session_content(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed(store, "s-1", title="My Title", user_text="first question", assistant_text="the answer")

    summaries = store.list_session_summaries()
    assert len(summaries) == 1
    summary = summaries[0]
    assert isinstance(summary, SessionSummary)
    assert summary.session_id == "s-1"
    assert summary.title == "My Title"
    assert summary.user_message_count == 1
    assert summary.message_count == 2
    assert summary.first_user_preview == "first question"
    assert summary.last_preview == "the answer"


def test_summary_refreshes_after_write(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed(store, "s-1", user_text="q1", assistant_text="a1")

    assert store.list_session_summaries()[0].last_preview == "a1"

    store.append_message(
        "s-1", role="user", parts=[MessagePart.text_part("q2")], status="complete"
    )
    refreshed = store.list_session_summaries()[0]
    # mtime changed -> summary cache must not serve the stale preview/count.
    assert refreshed.user_message_count == 2
    assert refreshed.last_preview == "q2"


def test_delete_removes_from_summaries_and_caches(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed(store, "s-1")
    store.get_session("s-1")  # populate parse cache
    assert store.list_session_summaries()  # populate summary cache

    assert store.delete_session("s-1") is True
    assert store.list_session_summaries() == []
    key = str(store._session_path("s-1"))
    assert key not in store._parse_cache
    assert key not in store._summary_cache


def test_unchanged_session_is_not_reparsed(tmp_path: Path, monkeypatch):
    # Seed with one instance, then read with a fresh one so the caches start
    # cold (the writing instance already warms its own cache).
    seed_store = FileSessionStore(tmp_path)
    _seed(seed_store, "s-1")
    store = FileSessionStore(tmp_path)

    calls = {"n": 0}
    real_load = fs_mod.json.load

    def counting_load(handle):
        calls["n"] += 1
        return real_load(handle)

    monkeypatch.setattr(fs_mod.json, "load", counting_load)

    first = store.get_session("s-1")
    assert calls["n"] == 1  # cold: parsed exactly once

    # Repeated reads of the unchanged file are cache hits: no extra json.load.
    for _ in range(5):
        again = store.get_session("s-1")
    assert calls["n"] == 1
    assert again.session_id == first.session_id

    # A write refreshes the cache in place, so the next read is still a hit.
    store.append_message(
        "s-1", role="user", parts=[MessagePart.text_part("more")], status="complete"
    )
    before = calls["n"]
    store.get_session("s-1")
    assert calls["n"] == before


def test_returned_session_is_isolated_from_cache(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed(store, "s-1", user_text="original")

    got = store.get_session("s-1")
    # Mutating a returned copy must never leak into the cached/stored state.
    got.title = "MUTATED"
    got.messages.clear()

    again = store.get_session("s-1")
    assert again.title != "MUTATED"
    assert len(again.messages) == 2
    assert store.read_history("s-1")[0].parts[0].text == "original"


def test_oversized_session_not_pinned_in_parse_cache(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    store._parse_cache_max_bytes = 256  # force the "too big to retain" path
    _seed(store, "s-big", user_text="x" * 4096)

    store.get_session("s-big")
    key = str(store._session_path("s-big"))
    assert key not in store._parse_cache  # not retained
    # Still fully correct on read.
    assert store.read_history("s-big")[0].parts[0].text == "x" * 4096


def test_display_name_parity_with_full_load(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    manager = RuntimeSessionManager(store=store)

    # custom name wins
    _seed(store, "s-custom", title="A Title", user_text="hello")
    store.update_session("s-custom", metadata={"custom_session_name": "  Pinned Name  "})
    # title only (no custom)
    _seed(store, "s-title", title="Just A Title", user_text="hello there")
    # first-user only (no title, no custom), long content to exercise truncation
    _seed(store, "s-firstuser", title=None, user_text="q" * 200, assistant_text=None)

    summaries = {s.session_id: s for s in store.list_session_summaries()}

    for session_id, summary in summaries.items():
        projection = {
            "metadata": (
                {"custom_session_name": summary.custom_name} if summary.custom_name else {}
            ),
            "title": summary.title,
            "history": (
                [{"role": "user", "content": summary.first_user_preview}]
                if summary.user_message_count
                else []
            ),
        }
        full = manager._session_to_legacy(store.get_session(session_id))
        assert resolve_session_display_name(projection) == resolve_session_display_name(full)


def test_manager_summaries_are_sorted_desc(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    manager = RuntimeSessionManager(store=store)
    _seed(store, "s-a")
    _seed(store, "s-b")
    # Touch s-a again so it becomes the most-recently-updated.
    store.append_message(
        "s-a", role="user", parts=[MessagePart.text_part("later")], status="complete"
    )

    summaries = asyncio.run(manager.list_session_summaries())
    expected = sorted(
        summaries, key=lambda s: (s.updated_at, s.session_id), reverse=True
    )
    assert [s.session_id for s in summaries] == [s.session_id for s in expected]
    # s-a was updated last, so it must sort first.
    assert summaries[0].session_id == "s-a"


def test_build_summary_counts_only_user_messages(tmp_path: Path):
    store = FileSessionStore(tmp_path)
    _seed(store, "s-1", user_text="u1", assistant_text="a1")
    store.append_message(
        "s-1", role="user", parts=[MessagePart.text_part("u2")], status="complete"
    )
    session = store.get_session("s-1")
    summary = build_session_summary(session)
    assert summary.user_message_count == 2
    assert summary.message_count == 3
    assert summary.last_preview == "u2"
