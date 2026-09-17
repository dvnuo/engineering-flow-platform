"""Built-in tool that searches and reads an assistant's earlier sessions.

The model gets two operations behind one tool id:

- ``session_search(query=...)`` ranks earlier sessions for a keyword query and
  quotes the member/assistant turns that matched.
- ``session_search(session_id=...)`` reads one earlier session as numbered
  member/assistant turns, paged with ``turn_offset``/``max_turns``.

Only member and assistant words are indexed. Tool calls, tool output, and
reasoning never appear in results, so nothing quoted here is machine noise.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from ...permissions import ALLOW, PermissionMetadata
from ...session.file_store import SessionSummary, build_session_summary
from ...session.protocol import SessionStore
from ...session.search import (
    ROLE_MEMBER,
    SCOPE_MINE,
    SCOPES,
    SessionMatch,
    SessionTranscript,
    list_session_summaries,
    search_transcripts,
    select_candidates,
    short_timestamp,
    transcript_from_messages,
)
from ...system_prompt import resolve_session_user
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


SESSION_SEARCH_TOOL_ID = "session_search"

DEFAULT_RESULT_LIMIT = 5
MAX_RESULT_LIMIT = 20
DEFAULT_MAX_TURNS = 20
MAX_MAX_TURNS = 40
DEFAULT_MAX_SCANNED_SESSIONS = 200
READ_TURN_CHAR_LIMIT = 1200
TRANSCRIPT_CACHE_MAX = 256

TOOL_DESCRIPTION = (
    "Search this assistant's earlier chat sessions (conversations other than "
    "the current one) or read one of them. Use it when the member refers to "
    "earlier work (\"last time\", \"the ticket we discussed\", \"as agreed "
    "before\") or asks what was decided or done earlier. "
    "Search: pass `query` with a few keywords; results quote the member and "
    "assistant turns that matched, never tool output. Read: pass `session_id` "
    "from a search result to get the numbered turns of that session, paging "
    "with `turn_offset` and `max_turns`. `scope` defaults to `mine` (sessions "
    "the current member took part in); use `agent` for every session of this "
    "assistant. Cite the session name and date of anything you reuse."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Keywords to search for across earlier sessions. Required "
                "unless session_id is given."
            ),
        },
        "session_id": {
            "type": "string",
            "description": (
                "Read this earlier session instead of searching. Take the id "
                "from a search result."
            ),
        },
        "scope": {
            "type": "string",
            "enum": list(SCOPES),
            "description": (
                "mine (default): sessions the current member took part in. "
                "agent: every session of this assistant."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_RESULT_LIMIT,
            "description": f"Maximum sessions to return from a search (default {DEFAULT_RESULT_LIMIT}).",
        },
        "turn_offset": {
            "type": "integer",
            "minimum": 0,
            "description": "Read mode: index of the first turn to return (default 0).",
        },
        "max_turns": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_MAX_TURNS,
            "description": f"Read mode: number of turns to return (default {DEFAULT_MAX_TURNS}).",
        },
    },
    "additionalProperties": False,
}


class TranscriptCache:
    """Bounded per-session transcript cache keyed by the session's header.

    ``updated_at`` and ``message_count`` change whenever a session changes, so
    an unchanged session is never re-parsed and a changed one is never served
    stale. Keeping only reduced transcripts, not sessions, is what keeps this
    small enough to hold hundreds of entries.
    """

    def __init__(self, max_entries: int = TRANSCRIPT_CACHE_MAX) -> None:
        self.max_entries = max(1, int(max_entries))
        self._entries: "OrderedDict[str, tuple[str, int, SessionTranscript]]" = OrderedDict()

    def get(
        self,
        summary: SessionSummary,
        loader: Callable[[SessionSummary], SessionTranscript],
    ) -> SessionTranscript:
        cached = self._entries.get(summary.session_id)
        if (
            cached is not None
            and cached[0] == summary.updated_at
            and cached[1] == summary.message_count
        ):
            self._entries.move_to_end(summary.session_id)
            return cached[2]
        transcript = loader(summary)
        self._entries[summary.session_id] = (
            summary.updated_at,
            summary.message_count,
            transcript,
        )
        self._entries.move_to_end(summary.session_id)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return transcript

    def __len__(self) -> int:
        return len(self._entries)


def create_session_search_tool(
    store: SessionStore,
    *,
    permission: PermissionMetadata | None = None,
    max_scanned_sessions: int = DEFAULT_MAX_SCANNED_SESSIONS,
    tool_id: str = SESSION_SEARCH_TOOL_ID,
) -> ToolDef:
    """Create the ``session_search`` tool over ``store``."""

    if max_scanned_sessions < 1:
        raise ValueError("max_scanned_sessions must be at least 1")
    cache = TranscriptCache()

    def load_transcript(summary: SessionSummary) -> SessionTranscript:
        return transcript_from_messages(summary, store.read_history(summary.session_id))

    def search(
        *,
        query: str,
        scope: str,
        limit: int,
        current_session_id: str | None,
        viewer_id: str | None,
    ) -> dict[str, Any]:
        selection = select_candidates(
            list_session_summaries(store),
            exclude_session_id=current_session_id,
            viewer_id=viewer_id,
            scope=scope,
            include_task_sessions=scope != SCOPE_MINE,
            limit=max_scanned_sessions,
        )
        transcripts = [cache.get(summary, load_transcript) for summary in selection.summaries]
        matches = search_transcripts(transcripts, query, limit=limit)
        return {
            "mode": "search",
            "query": query,
            "scope": selection.scope,
            "scope_note": selection.scope_note,
            "scanned_sessions": len(transcripts),
            "sessions_in_scope": selection.total_in_scope,
            "results": [_match_payload(match) for match in matches],
        }

    def read(
        *,
        session_id: str,
        turn_offset: int,
        max_turns: int,
        current_session_id: str | None,
    ) -> dict[str, Any]:
        if current_session_id and session_id == current_session_id:
            raise LookupError(
                "That is the current session; its history is already in your context."
            )
        summary = _session_summary(store, session_id)
        if summary is None:
            raise LookupError(f"Unknown session_id: {session_id}")
        transcript = cache.get(summary, load_transcript)
        turns = transcript.turns[turn_offset : turn_offset + max_turns]
        next_offset = turn_offset + len(turns)
        return {
            "mode": "read",
            "session_id": transcript.session_id,
            "name": transcript.name,
            "created_at": transcript.created_at,
            "updated_at": transcript.updated_at,
            "authors": list(transcript.author_names),
            "total_turns": len(transcript.turns),
            "turn_offset": turn_offset,
            "next_turn_offset": next_offset if next_offset < len(transcript.turns) else None,
            "turns": [_turn_payload(turn, char_limit=READ_TURN_CHAR_LIMIT) for turn in turns],
        }

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        query = _clean_text(args.get("query"))
        session_id = _clean_text(args.get("session_id"))
        scope = _clean_text(args.get("scope")) or SCOPE_MINE
        limit = _bounded_int(args.get("limit"), default=DEFAULT_RESULT_LIMIT, low=1, high=MAX_RESULT_LIMIT)
        turn_offset = _bounded_int(args.get("turn_offset"), default=0, low=0, high=None)
        max_turns = _bounded_int(args.get("max_turns"), default=DEFAULT_MAX_TURNS, low=1, high=MAX_MAX_TURNS)
        viewer_id = _viewer_id(context.metadata)

        if not query and not session_id:
            return _error_result(
                context,
                tool_id,
                "Pass `query` to search earlier sessions or `session_id` to read one.",
            )
        try:
            if session_id:
                payload = await asyncio.to_thread(
                    read,
                    session_id=session_id,
                    turn_offset=turn_offset,
                    max_turns=max_turns,
                    current_session_id=context.session_id,
                )
                content = _render_read(payload)
            else:
                payload = await asyncio.to_thread(
                    search,
                    query=query,
                    scope=scope,
                    limit=limit,
                    current_session_id=context.session_id,
                    viewer_id=viewer_id,
                )
                content = _render_search(payload, tool_id=tool_id)
        except LookupError as exc:
            return _error_result(context, tool_id, str(exc))

        metadata = _result_metadata(payload)
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name=tool_id,
            status="success",
            success=True,
            content=content,
            output=payload,
            metadata=metadata,
        )

    return ToolDef(
        id=tool_id,
        description=TOOL_DESCRIPTION,
        input_schema=INPUT_SCHEMA,
        execute=execute,
        permission=permission
        or PermissionMetadata(
            action=ALLOW,
            category="memory",
            resource="session",
            risk="low",
        ),
        runtime_metadata={
            "session_store": store,
            "transcript_cache": cache,
        },
    )


def _session_summary(store: Any, session_id: str) -> SessionSummary | None:
    getter = getattr(store, "get_session_summary", None)
    try:
        if callable(getter):
            return getter(session_id)
        return build_session_summary(store.get_session(session_id))
    except (KeyError, ValueError):
        return None


def _viewer_id(metadata: Mapping[str, Any] | None) -> str | None:
    identity = resolve_session_user(metadata)
    if identity is None:
        return None
    return identity.get("id") or None


def _match_payload(match: SessionMatch) -> dict[str, Any]:
    transcript = match.transcript
    return {
        "session_id": transcript.session_id,
        "name": transcript.name,
        "created_at": transcript.created_at,
        "updated_at": transcript.updated_at,
        "authors": list(transcript.author_names),
        "member_turns": transcript.member_turn_count,
        "score": round(match.score, 3),
        "matched_terms": match.matched_terms,
        "excerpts": [
            {
                "turn": excerpt.turn.index,
                "role": excerpt.turn.role,
                "created_at": excerpt.turn.created_at,
                "author": excerpt.turn.author_name,
                "text": excerpt.text,
            }
            for excerpt in match.excerpts
        ],
    }


def _turn_payload(turn: Any, *, char_limit: int) -> dict[str, Any]:
    text = turn.text
    truncated = len(text) > char_limit
    if truncated:
        text = text[:char_limit]
    return {
        "turn": turn.index,
        "role": turn.role,
        "created_at": turn.created_at,
        "author": turn.author_name,
        "text": text,
        "truncated": truncated,
        "original_chars": len(turn.text),
    }


def _render_search(payload: Mapping[str, Any], *, tool_id: str) -> str:
    results = payload.get("results") or []
    header = (
        f"{len(results)} earlier session(s) matched \"{payload.get('query')}\" "
        f"(scope: {payload.get('scope')}, {payload.get('scanned_sessions')} of "
        f"{payload.get('sessions_in_scope')} sessions in scope scanned)."
    )
    lines = [header]
    note = payload.get("scope_note")
    if note:
        lines.append(f"Note: {note}")
    if not results:
        lines.append(
            "No earlier session mentions these keywords. Try other words, or "
            "scope=\"agent\" to include every session of this assistant."
        )
        return "\n".join(lines)
    lines.append("")
    for position, result in enumerate(results, 1):
        authors = ", ".join(result.get("authors") or []) or "unknown member"
        lines.append(
            f"{position}. \"{result.get('name')}\" — session_id: {result.get('session_id')} — "
            f"updated {short_timestamp(result.get('updated_at'))} — "
            f"{result.get('member_turns')} member turn(s) — with {authors}"
        )
        for excerpt in result.get("excerpts") or []:
            who = excerpt.get("role")
            if who == ROLE_MEMBER and excerpt.get("author"):
                who = f"member {excerpt.get('author')}"
            lines.append(
                f"   [turn {excerpt.get('turn')} · {who} · "
                f"{short_timestamp(excerpt.get('created_at'))}] {excerpt.get('text')}"
            )
    lines.append("")
    lines.append(
        f"Read a session with {tool_id}(session_id=...). Cite the session name "
        "and date when you reuse what you find."
    )
    return "\n".join(lines)


def _render_read(payload: Mapping[str, Any]) -> str:
    turns = payload.get("turns") or []
    total = int(payload.get("total_turns") or 0)
    offset = int(payload.get("turn_offset") or 0)
    authors = ", ".join(payload.get("authors") or []) or "unknown member"
    first = offset + 1 if turns else 0
    last = offset + len(turns)
    lines = [
        f"Session \"{payload.get('name')}\" — session_id: {payload.get('session_id')} — "
        f"created {short_timestamp(payload.get('created_at'))} — "
        f"updated {short_timestamp(payload.get('updated_at'))} — members: {authors}",
        f"Turns {first}-{last} of {total}."
        + (
            f" Continue with turn_offset={payload.get('next_turn_offset')}."
            if payload.get("next_turn_offset") is not None
            else ""
        ),
    ]
    if not turns:
        lines.append("No turns in this range.")
        return "\n".join(lines)
    for turn in turns:
        who = turn.get("role")
        if who == ROLE_MEMBER and turn.get("author"):
            who = f"member {turn.get('author')}"
        lines.append("")
        lines.append(f"[turn {turn.get('turn')} · {who} · {short_timestamp(turn.get('created_at'))}]")
        lines.append(str(turn.get("text") or ""))
        if turn.get("truncated"):
            lines.append(
                f"[… turn truncated to {READ_TURN_CHAR_LIMIT} of {turn.get('original_chars')} chars]"
            )
    return "\n".join(lines)


def _result_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"mode": payload.get("mode")}
    if payload.get("mode") == "search":
        metadata.update(
            {
                "query": payload.get("query"),
                "scope": payload.get("scope"),
                "scanned_sessions": payload.get("scanned_sessions"),
                "matched_sessions": len(payload.get("results") or []),
                "session_ids": [
                    item.get("session_id") for item in payload.get("results") or []
                ],
            }
        )
    else:
        metadata.update(
            {
                "session_id": payload.get("session_id"),
                "turn_offset": payload.get("turn_offset"),
                "returned_turns": len(payload.get("turns") or []),
                "total_turns": payload.get("total_turns"),
            }
        )
    return metadata


def _error_result(context: ToolContext, tool_id: str, message: str) -> ToolResult:
    return ToolResult(
        call_id=context.tool_call_id or "",
        tool_name=tool_id,
        status="error",
        success=False,
        error=message,
        content=message,
        output={"error": message},
        metadata={"error": message},
    )


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _bounded_int(value: Any, *, default: int, low: int, high: int | None) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    number = max(low, number)
    if high is not None:
        number = min(high, number)
    return number


__all__ = [
    "DEFAULT_MAX_SCANNED_SESSIONS",
    "SESSION_SEARCH_TOOL_ID",
    "TOOL_DESCRIPTION",
    "TranscriptCache",
    "create_session_search_tool",
]
