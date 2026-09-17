"""Keyword search over an assistant's earlier sessions.

This is the cross-session memory primitive: it turns stored sessions into
member/assistant transcripts, scopes them to the member behind a run, and
ranks them for a keyword query. Tool output, reasoning, and tool calls are
never part of a transcript, so a match always points at something a person or
the assistant actually said.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math
import re
from typing import Any

from .file_store import SessionSummary, build_session_summary
from .models import Message, MessagePartType, MessageRole


# Sessions the gateway creates for background work rather than for a chat.
# Shared with the gateway's session listing so both agree on what a "task
# session" is.
TASK_SESSION_ID_PREFIXES = (
    "agent-task:",
    "agent-task-",
    "generic-task:",
    "generic-task-",
    "delegation:",
    "delegation-",
    "task-",
)

# User-role messages the runtime itself appends. They carry no member words,
# so they are neither searched nor shown as member turns.
SYNTHETIC_USER_SOURCES = frozenset({"background_task.injected", "compaction.replay"})

SCOPE_MINE = "mine"
SCOPE_AGENT = "agent"
SCOPES = (SCOPE_MINE, SCOPE_AGENT)

ROLE_MEMBER = "member"
ROLE_ASSISTANT = "assistant"
ROLE_SUMMARY = "summary"

# Bounds that keep one transcript small enough to cache many of them.
MAX_TURN_CHARS = 4000
MAX_TURNS_PER_SESSION = 400
DISPLAY_NAME_PREVIEW_CHARS = 30

_CJK_RANGE = "぀-ヿ㐀-䶿一-鿿가-힯"
_TERM_PATTERN = re.compile(rf"[{_CJK_RANGE}]+|\w+", re.UNICODE)
_CJK_PATTERN = re.compile(rf"^[{_CJK_RANGE}]+$")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def is_task_session_id(session_id: Any) -> bool:
    if not isinstance(session_id, str):
        return False
    return session_id.strip().startswith(TASK_SESSION_ID_PREFIXES)


@dataclass(frozen=True)
class SessionTurn:
    """One member, assistant, or summary turn of a transcript."""

    index: int
    role: str
    text: str
    created_at: str
    message_id: str = ""
    author_id: str | None = None
    author_name: str | None = None


@dataclass(frozen=True)
class SessionTranscript:
    """The searchable view of one session."""

    session_id: str
    name: str
    created_at: str
    updated_at: str
    turns: tuple[SessionTurn, ...]
    author_ids: tuple[str, ...]
    author_names: tuple[str, ...]

    @property
    def member_turn_count(self) -> int:
        return sum(1 for turn in self.turns if turn.role == ROLE_MEMBER)


@dataclass(frozen=True)
class SearchTerm:
    text: str
    weight: float
    pattern: re.Pattern[str] | None


@dataclass(frozen=True)
class TurnExcerpt:
    turn: SessionTurn
    text: str
    score: float


@dataclass(frozen=True)
class SessionMatch:
    transcript: SessionTranscript
    score: float
    matched_terms: int
    excerpts: tuple[TurnExcerpt, ...]


@dataclass(frozen=True)
class CandidateSelection:
    """Sessions in scope for a search, plus how the scope was resolved."""

    summaries: tuple[SessionSummary, ...]
    scope: str
    scope_note: str | None
    total_in_scope: int


def session_display_name(summary: SessionSummary) -> str:
    """Mirror the gateway's display-name precedence: custom name, title, first words."""

    if summary.custom_name:
        return summary.custom_name
    title = (summary.title or "").strip()
    if title:
        return title
    preview = _single_line(summary.first_user_preview)
    if preview:
        return _truncate_with_ellipsis(preview, DISPLAY_NAME_PREVIEW_CHARS)
    return "New Chat"


def list_session_summaries(store: Any) -> list[SessionSummary]:
    """Session headers from any store, without loading bodies where avoidable."""

    lister = getattr(store, "list_session_summaries", None)
    if callable(lister):
        return list(lister())
    return [build_session_summary(session) for session in store.list_sessions()]


def select_candidates(
    summaries: Iterable[SessionSummary],
    *,
    exclude_session_id: str | None,
    viewer_id: str | None,
    scope: str = SCOPE_MINE,
    include_task_sessions: bool = False,
    limit: int | None = None,
) -> CandidateSelection:
    """Pick the sessions a search or overview may look at, newest first.

    ``mine`` keeps sessions the viewer took part in. Without a viewer identity
    (local development, no Portal) there is nothing to filter by, so the
    selection widens to the whole assistant and says so in ``scope_note``.
    """

    if scope not in SCOPES:
        raise ValueError(f"scope must be one of: {', '.join(SCOPES)}")
    effective_scope = scope
    scope_note: str | None = None
    if scope == SCOPE_MINE and not viewer_id:
        effective_scope = SCOPE_AGENT
        scope_note = (
            "No member identity was attached to this run, so every session of "
            "this assistant was considered."
        )

    selected: list[SessionSummary] = []
    for summary in summaries:
        if exclude_session_id and summary.session_id == exclude_session_id:
            continue
        if summary.user_message_count <= 0:
            continue
        if not include_task_sessions and is_task_session_id(summary.session_id):
            continue
        if effective_scope == SCOPE_MINE and viewer_id not in summary.author_ids:
            continue
        selected.append(summary)
    selected.sort(key=lambda item: (item.updated_at, item.session_id), reverse=True)
    total = len(selected)
    if limit is not None:
        selected = selected[: max(0, int(limit))]
    return CandidateSelection(
        summaries=tuple(selected),
        scope=effective_scope,
        scope_note=scope_note,
        total_in_scope=total,
    )


def transcript_from_messages(
    summary: SessionSummary,
    messages: Iterable[Message],
    *,
    max_turn_chars: int = MAX_TURN_CHARS,
    max_turns: int = MAX_TURNS_PER_SESSION,
) -> SessionTranscript:
    """Reduce stored messages to member/assistant/summary turns."""

    turns: list[SessionTurn] = []
    author_ids: list[str] = []
    author_names: list[str] = []
    for message in messages:
        if len(turns) >= max_turns:
            break
        turn = _turn_from_message(len(turns) + 1, message, max_turn_chars=max_turn_chars)
        if turn is None:
            continue
        turns.append(turn)
        if turn.author_id and turn.author_id not in author_ids:
            author_ids.append(turn.author_id)
        if turn.author_name and turn.author_name not in author_names:
            author_names.append(turn.author_name)
    return SessionTranscript(
        session_id=summary.session_id,
        name=session_display_name(summary),
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        turns=tuple(turns),
        author_ids=tuple(author_ids) or tuple(summary.author_ids),
        author_names=tuple(author_names) or tuple(summary.author_names),
    )


def parse_query(query: str) -> tuple[list[SearchTerm], str]:
    """Split a keyword query into scored terms plus the normalized phrase.

    Latin/digit words match on word boundaries so ``test`` does not hit
    ``latest``. CJK runs have no word boundaries, so they match as substrings,
    and runs longer than two characters also contribute their bigrams at a low
    weight so a slightly different phrasing still finds the session.
    """

    phrase = _single_line(query).lower()
    terms: dict[str, SearchTerm] = {}

    def add(text: str, weight: float, pattern: re.Pattern[str] | None) -> None:
        existing = terms.get(text)
        if existing is None or existing.weight < weight:
            terms[text] = SearchTerm(text=text, weight=weight, pattern=pattern)

    for token in _TERM_PATTERN.findall(phrase):
        if _CJK_PATTERN.match(token):
            add(token, 1.0, None)
            if len(token) > 2:
                for start in range(len(token) - 1):
                    add(token[start : start + 2], 0.3, None)
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        add(token, 1.0, re.compile(rf"(?<!\w){re.escape(token)}(?!\w)", re.UNICODE))
    return list(terms.values()), phrase


def search_transcripts(
    transcripts: Iterable[SessionTranscript],
    query: str,
    *,
    limit: int = 5,
    excerpts_per_session: int = 3,
) -> list[SessionMatch]:
    """Rank transcripts for ``query`` and pick the turns worth quoting."""

    terms, phrase = parse_query(query)
    if not terms:
        return []
    matches: list[SessionMatch] = []
    for transcript in transcripts:
        scored: list[TurnExcerpt] = []
        matched_terms: set[str] = set()
        for turn in transcript.turns:
            score, matched = _score_turn(turn, terms, phrase)
            if score <= 0:
                continue
            matched_terms.update(matched)
            scored.append(
                TurnExcerpt(
                    turn=turn,
                    text=make_excerpt(turn.text, terms, phrase),
                    score=score,
                )
            )
        if not scored:
            continue
        scored.sort(key=lambda item: (item.score, item.turn.index), reverse=True)
        best = scored[: max(1, excerpts_per_session)]
        session_score = sum(item.score for item in best) + 0.5 * len(matched_terms)
        matches.append(
            SessionMatch(
                transcript=transcript,
                score=session_score,
                matched_terms=len(matched_terms),
                excerpts=tuple(sorted(best, key=lambda item: item.turn.index)),
            )
        )
    matches.sort(
        key=lambda item: (item.score, item.transcript.updated_at),
        reverse=True,
    )
    return matches[: max(0, limit)]


def make_excerpt(
    text: str,
    terms: Iterable[SearchTerm],
    phrase: str,
    *,
    before: int = 100,
    after: int = 180,
) -> str:
    """A short window of ``text`` around its first match."""

    flat = _single_line(text)
    lowered = flat.lower()
    position = -1
    if phrase:
        position = lowered.find(phrase)
    if position < 0:
        for term in terms:
            found = _find_term(lowered, term)
            if found >= 0 and (position < 0 or found < position):
                position = found
    if position < 0:
        position = 0
    start = max(0, position - before)
    end = min(len(flat), position + after)
    if start > 0:
        space = flat.rfind(" ", start, position)
        if space > start:
            start = space + 1
    if end < len(flat):
        space = flat.find(" ", end)
        if 0 <= space < end + 40:
            end = space
    excerpt = flat[start:end].strip()
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(flat):
        excerpt = excerpt + "…"
    return excerpt


def recent_sessions_overview(
    summaries: Iterable[SessionSummary],
    *,
    exclude_session_id: str | None,
    viewer_id: str | None,
    limit: int = 8,
) -> dict[str, Any]:
    """The compact "earlier sessions" listing rendered into the system prompt."""

    selection = select_candidates(
        summaries,
        exclude_session_id=exclude_session_id,
        viewer_id=viewer_id,
        scope=SCOPE_MINE,
        limit=limit,
    )
    return {
        "scope": selection.scope,
        "scope_note": selection.scope_note,
        "total_in_scope": selection.total_in_scope,
        "sessions": [
            {
                "session_id": summary.session_id,
                "name": session_display_name(summary),
                "updated_at": summary.updated_at,
                "member_turns": summary.user_message_count,
                "authors": list(summary.author_names),
            }
            for summary in selection.summaries
        ],
    }


def short_timestamp(value: Any) -> str:
    """``2026-09-15T10:22:33.1Z`` -> ``2026-09-15 10:22`` for prompt and tool text."""

    text = str(value or "").strip()
    if len(text) >= 16 and text[10] == "T":
        return text[:10] + " " + text[11:16]
    return text[:16]


def _turn_from_message(
    index: int,
    message: Message,
    *,
    max_turn_chars: int,
) -> SessionTurn | None:
    metadata = message.metadata if isinstance(message.metadata, Mapping) else {}
    if message.role is MessageRole.USER:
        if metadata.get("source") in SYNTHETIC_USER_SOURCES:
            return None
        original = metadata.get("original_user_message")
        text = original if isinstance(original, str) and original.strip() else _text_parts(message)
        if not text.strip():
            return None
        return SessionTurn(
            index=index,
            role=ROLE_MEMBER,
            text=_bounded(text, max_turn_chars),
            created_at=message.created_at,
            message_id=message.message_id,
            author_id=_optional_text(metadata.get("author_id")),
            author_name=_optional_text(metadata.get("author_name")),
        )
    if message.role is MessageRole.ASSISTANT:
        text = _text_parts(message)
        if not text.strip():
            return None
        return SessionTurn(
            index=index,
            role=ROLE_ASSISTANT,
            text=_bounded(text, max_turn_chars),
            created_at=message.created_at,
            message_id=message.message_id,
        )
    summary = _compaction_summary(message)
    if summary:
        return SessionTurn(
            index=index,
            role=ROLE_SUMMARY,
            text=_bounded(summary, max_turn_chars),
            created_at=message.created_at,
            message_id=message.message_id,
        )
    return None


def _text_parts(message: Message) -> str:
    chunks = [
        part.text
        for part in message.parts
        if part.type is MessagePartType.TEXT and isinstance(part.text, str) and part.text.strip()
    ]
    return "\n".join(chunks)


def _compaction_summary(message: Message) -> str:
    for part in message.parts:
        if part.type is MessagePartType.COMPACTION and part.compaction is not None:
            summary = part.compaction.summary
            if isinstance(summary, str) and summary.strip():
                return summary
    return ""


def _score_turn(
    turn: SessionTurn,
    terms: list[SearchTerm],
    phrase: str,
) -> tuple[float, set[str]]:
    lowered = turn.text.lower()
    score = 0.0
    matched: set[str] = set()
    for term in terms:
        count = _count_term(lowered, term)
        if count <= 0:
            continue
        if term.weight >= 1.0:
            matched.add(term.text)
        score += term.weight * (1.0 + math.log(count))
    if score <= 0:
        return 0.0, matched
    if phrase and len(terms) > 1 and phrase in lowered:
        score += 2.0
    if turn.role == ROLE_MEMBER:
        score *= 1.2
    return score, matched


def _count_term(lowered: str, term: SearchTerm) -> int:
    if term.pattern is None:
        return lowered.count(term.text)
    return len(term.pattern.findall(lowered))


def _find_term(lowered: str, term: SearchTerm) -> int:
    if term.pattern is None:
        return lowered.find(term.text)
    found = term.pattern.search(lowered)
    return found.start() if found else -1


def _single_line(value: Any) -> str:
    return _WHITESPACE_PATTERN.sub(" ", str(value or "")).strip()


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _truncate_with_ellipsis(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


__all__ = [
    "CandidateSelection",
    "ROLE_ASSISTANT",
    "ROLE_MEMBER",
    "ROLE_SUMMARY",
    "SCOPES",
    "SCOPE_AGENT",
    "SCOPE_MINE",
    "SYNTHETIC_USER_SOURCES",
    "SearchTerm",
    "SessionMatch",
    "SessionTranscript",
    "SessionTurn",
    "TASK_SESSION_ID_PREFIXES",
    "TurnExcerpt",
    "is_task_session_id",
    "list_session_summaries",
    "make_excerpt",
    "parse_query",
    "recent_sessions_overview",
    "search_transcripts",
    "select_candidates",
    "session_display_name",
    "short_timestamp",
    "transcript_from_messages",
]
