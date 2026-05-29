"""Post-hoc pruning of old persisted tool result output."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..session.models import Message, MessagePart, MessagePartType, MessageRole
from ..types import ToolResult, utc_now_iso


DEFAULT_PROTECTED_TOOLS = frozenset({"skill"})
PRUNE_MARKER_TEMPLATE = (
    "[Old tool result content cleared for context compaction: omitted {chars} chars]"
)


@dataclass(frozen=True)
class ToolOutputPruneResult:
    """Result of clearing old tool result content from copied messages."""

    messages: list[Message]
    pruned_result_count: int = 0
    pruned_chars: int = 0
    protected_chars: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _PruneCandidate:
    part: MessagePart
    result: ToolResult
    original_chars: int


def prune_old_tool_outputs(
    messages: Iterable[Message],
    *,
    protect_recent_chars: int = 40000,
    min_pruned_chars: int = 20000,
    output_max_chars: int = 2000,
    protected_tools: frozenset[str] = DEFAULT_PROTECTED_TOOLS,
) -> ToolOutputPruneResult:
    """Prune old completed tool result content from copied session history.

    The latest two user turns are always protected. Older completed tool
    results are walked from newest to oldest; the newest eligible output up to
    ``protect_recent_chars`` is kept, and only older eligible output is pruned.
    """

    protect_recent_chars = _non_negative_int(
        protect_recent_chars,
        "protect_recent_chars",
    )
    min_pruned_chars = _non_negative_int(min_pruned_chars, "min_pruned_chars")
    output_max_chars = _non_negative_int(output_max_chars, "output_max_chars")
    protected_tool_names = frozenset(str(tool) for tool in protected_tools)

    copied_messages = deepcopy(list(messages))
    candidates: list[_PruneCandidate] = []
    protected_chars = 0
    total_counted_chars = 0
    skipped_protected_tools = 0
    skipped_already_pruned = 0
    user_turns_seen = 0

    for message in reversed(copied_messages):
        if message.role is MessageRole.USER:
            user_turns_seen += 1
        if user_turns_seen < 2:
            continue

        for part in reversed(message.parts):
            if part.type is not MessagePartType.TOOL_RESULT:
                continue
            result = part.tool_result
            if result is None or not _is_completed_tool_result(result):
                continue
            if result.tool_name in protected_tool_names:
                skipped_protected_tools += 1
                continue
            if _is_compaction_pruned(part.metadata) or _is_compaction_pruned(
                result.metadata
            ):
                skipped_already_pruned += 1
                continue

            original_chars = len(result.content or "")
            if original_chars <= 0:
                continue

            total_counted_chars += original_chars
            if total_counted_chars <= protect_recent_chars:
                protected_chars += original_chars
                continue

            candidates.append(
                _PruneCandidate(
                    part=part,
                    result=result,
                    original_chars=original_chars,
                )
            )

    candidate_chars = sum(candidate.original_chars for candidate in candidates)
    if candidate_chars <= min_pruned_chars:
        return ToolOutputPruneResult(
            messages=copied_messages,
            protected_chars=protected_chars,
            metadata=_result_metadata(
                pruned_result_count=0,
                pruned_chars=0,
                protected_chars=protected_chars,
                candidate_chars=candidate_chars,
                protect_recent_chars=protect_recent_chars,
                min_pruned_chars=min_pruned_chars,
                output_max_chars=output_max_chars,
                protected_tools=protected_tool_names,
                skipped_protected_tools=skipped_protected_tools,
                skipped_already_pruned=skipped_already_pruned,
            ),
        )

    pruned_chars = 0
    pruned_at = utc_now_iso()
    for candidate in candidates:
        pruned_chars += _prune_candidate(
            candidate,
            output_max_chars=output_max_chars,
            pruned_at=pruned_at,
        )

    return ToolOutputPruneResult(
        messages=copied_messages,
        pruned_result_count=len(candidates),
        pruned_chars=pruned_chars,
        protected_chars=protected_chars,
        metadata=_result_metadata(
            pruned_result_count=len(candidates),
            pruned_chars=pruned_chars,
            protected_chars=protected_chars,
            candidate_chars=candidate_chars,
            protect_recent_chars=protect_recent_chars,
            min_pruned_chars=min_pruned_chars,
            output_max_chars=output_max_chars,
            protected_tools=protected_tool_names,
            skipped_protected_tools=skipped_protected_tools,
            skipped_already_pruned=skipped_already_pruned,
            compaction_pruned_at=pruned_at,
        ),
    )


def _prune_candidate(
    candidate: _PruneCandidate,
    *,
    output_max_chars: int,
    pruned_at: str,
) -> int:
    result = candidate.result
    preview = (result.content or "")[:output_max_chars]
    omitted_chars = max(0, candidate.original_chars - len(preview))
    marker = PRUNE_MARKER_TEMPLATE.format(chars=omitted_chars)
    compacted_content = f"{preview}\n\n{marker}" if preview else marker

    result.content = compacted_content
    if isinstance(result.output, str):
        result.output = compacted_content
    result.truncated = True

    marker_metadata = {
        "compaction_pruned": True,
        "compaction_pruned_at": pruned_at,
        "original_chars": candidate.original_chars,
        "omitted_chars": omitted_chars,
        "output_max_chars": output_max_chars,
    }
    result.metadata.update(marker_metadata)
    candidate.part.metadata.update(marker_metadata)
    return omitted_chars


def _result_metadata(
    *,
    pruned_result_count: int,
    pruned_chars: int,
    protected_chars: int,
    candidate_chars: int,
    protect_recent_chars: int,
    min_pruned_chars: int,
    output_max_chars: int,
    protected_tools: frozenset[str],
    skipped_protected_tools: int,
    skipped_already_pruned: int,
    compaction_pruned_at: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "compaction_pruned": pruned_result_count > 0,
        "pruned_result_count": pruned_result_count,
        "pruned_chars": pruned_chars,
        "candidate_chars": candidate_chars,
        "protected_chars": protected_chars,
        "protect_recent_chars": protect_recent_chars,
        "min_pruned_chars": min_pruned_chars,
        "output_max_chars": output_max_chars,
        "protected_tools": sorted(protected_tools),
        "skipped_protected_tool_count": skipped_protected_tools,
        "skipped_already_pruned_count": skipped_already_pruned,
    }
    if compaction_pruned_at is not None:
        metadata["compaction_pruned_at"] = compaction_pruned_at
    return metadata


def _is_completed_tool_result(result: ToolResult) -> bool:
    return result.success and result.status in {"success", "complete", "completed"}


def _is_compaction_pruned(metadata: Mapping[str, Any]) -> bool:
    return any(
        metadata.get(key) is True
        for key in (
            "compaction_pruned",
            "compaction_compacted",
            "compacted",
            "pruned",
        )
    )


def _non_negative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


__all__ = ["ToolOutputPruneResult", "prune_old_tool_outputs"]
