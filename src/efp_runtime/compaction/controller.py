"""Injectable compaction summarizer control for Runtime v2."""

from __future__ import annotations

from collections.abc import Awaitable, Iterable, Mapping
from dataclasses import dataclass, field
import inspect
from typing import Any, Optional, Protocol, Union

from ..session.models import Message
from .strategy import BudgetCompactionStrategy, CompactionResult, ContextBudget


@dataclass(frozen=True)
class CompactionRequest:
    """Source context passed to a compaction summarizer."""

    session_id: str
    messages: list[Message]
    compacted_messages: list[Message]
    kept_messages: list[Message]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", list(self.messages))
        object.__setattr__(self, "compacted_messages", list(self.compacted_messages))
        object.__setattr__(self, "kept_messages", list(self.kept_messages))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class CompactionSummary:
    """Model-visible summary returned by a compaction summarizer."""

    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


CompactionSummaryValue = Union[CompactionSummary, str]
CompactionSummaryReturn = Union[
    CompactionSummaryValue,
    Awaitable[CompactionSummaryValue],
]


class CompactionSummarizer(Protocol):
    """Callable boundary for sync or async compaction summarizers."""

    def __call__(self, request: CompactionRequest) -> CompactionSummaryReturn:
        ...


@dataclass(frozen=True)
class CompactionPreparation:
    """Planned compaction plus optional summarizer override."""

    result: CompactionResult
    summary: Optional[str] = None
    summary_metadata: dict[str, Any] = field(default_factory=dict)
    compaction_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def compaction_applied(self) -> bool:
        return self.result.compacted


class DeterministicCompactionSummarizer:
    """Stable fallback summarizer matching the deterministic strategy summary."""

    def __call__(self, request: CompactionRequest) -> CompactionSummary:
        part_count = int(
            request.metadata.get("compacted_part_count")
            or sum(len(message.parts) for message in request.compacted_messages)
        )
        message_count = int(
            request.metadata.get("compacted_message_count")
            or len(request.compacted_messages)
        )
        tool_pair_count = int(request.metadata.get("compacted_tool_pair_count") or 0)
        return CompactionSummary(
            summary=(
                f"Compacted {part_count} message part(s) from {message_count} "
                f"message(s). Tool call/result pair(s) compacted: {tool_pair_count}."
            ),
            metadata={"summary_source": "deterministic"},
        )


class CompactionController:
    """Plan budget compaction and invoke a summarizer only when needed."""

    def __init__(
        self,
        summarizer: Optional[CompactionSummarizer] = None,
    ) -> None:
        self.summarizer = summarizer or DeterministicCompactionSummarizer()

    async def prepare(
        self,
        messages: Iterable[Message],
        *,
        session_id: str,
        budget: Optional[ContextBudget] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        compaction_strategy: Optional[BudgetCompactionStrategy] = None,
    ) -> CompactionPreparation:
        source_messages = list(messages)
        strategy = compaction_strategy or BudgetCompactionStrategy(
            budget=budget or ContextBudget()
        )
        resolved_budget = _strategy_budget(strategy)
        result = strategy.compact(source_messages)
        if not result.compacted:
            return CompactionPreparation(result=result)

        compaction_metadata = _compaction_metadata(
            budget=resolved_budget,
            result=result,
        )
        request_metadata = dict(metadata or {})
        request_metadata.update(compaction_metadata)
        request = CompactionRequest(
            session_id=session_id,
            messages=source_messages,
            compacted_messages=result.compacted_messages,
            kept_messages=result.kept_messages,
            metadata=request_metadata,
        )
        summary, summary_metadata = await _invoke_summarizer(
            self.summarizer,
            request,
        )
        compaction_metadata.update(summary_metadata)
        return CompactionPreparation(
            result=result,
            summary=summary,
            summary_metadata=summary_metadata,
            compaction_metadata=compaction_metadata,
        )


async def maybe_summarize_compaction(
    messages: Iterable[Message],
    *,
    session_id: str,
    budget: Optional[ContextBudget] = None,
    summarizer: Optional[CompactionSummarizer] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    compaction_strategy: Optional[BudgetCompactionStrategy] = None,
) -> CompactionPreparation:
    """Plan compaction and return a summary override when compaction applies."""

    controller = CompactionController(summarizer)
    return await controller.prepare(
        messages,
        session_id=session_id,
        budget=budget,
        metadata=metadata,
        compaction_strategy=compaction_strategy,
    )


async def _invoke_summarizer(
    summarizer: CompactionSummarizer,
    request: CompactionRequest,
) -> tuple[str, dict[str, Any]]:
    summarizer_info = {
        "used": True,
        "fallback": False,
        "type": _summarizer_name(summarizer),
    }
    try:
        raw_summary = summarizer(request)
        if inspect.isawaitable(raw_summary):
            raw_summary = await raw_summary
        summary = _coerce_summary(raw_summary)
    except Exception as exc:  # noqa: BLE001 - compaction falls back deterministically.
        fallback = DeterministicCompactionSummarizer()(request)
        summarizer_info.update(
            {
                "fallback": True,
                "error_type": exc.__class__.__name__,
                "summarizer_error": _exception_text(exc),
            }
        )
        summary_metadata = dict(fallback.metadata)
        summary_metadata["summarizer"] = summarizer_info
        return fallback.summary, summary_metadata

    summary_metadata = dict(summary.metadata)
    summary_metadata["summarizer"] = summarizer_info
    return summary.summary, summary_metadata


def _coerce_summary(value: Any) -> CompactionSummary:
    if isinstance(value, CompactionSummary):
        return value
    if isinstance(value, str):
        return CompactionSummary(summary=value)
    raise TypeError(
        "compaction summarizer must return CompactionSummary or str, "
        f"got {value.__class__.__name__}"
    )


def _compaction_metadata(
    *,
    budget: ContextBudget,
    result: CompactionResult,
) -> dict[str, Any]:
    return {
        "max_parts": budget.max_parts,
        "max_chars": budget.max_chars,
        "reserve_chars": budget.reserve_chars,
        "compacted_part_count": result.compacted_part_count,
        "compacted_message_count": result.compacted_message_count,
        "compacted_tool_pair_count": result.compacted_tool_pair_count,
        "compacted_chars": result.compacted_chars,
        "kept_chars": result.kept_chars,
    }


def _strategy_budget(strategy: BudgetCompactionStrategy) -> ContextBudget:
    budget = getattr(strategy, "budget", None)
    if isinstance(budget, ContextBudget):
        return budget
    return ContextBudget(
        max_parts=getattr(strategy, "max_parts", None),
        max_chars=getattr(strategy, "max_chars", None),
        reserve_chars=getattr(strategy, "reserve_chars", 0),
    )


def _summarizer_name(summarizer: CompactionSummarizer) -> str:
    name = getattr(summarizer, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return summarizer.__class__.__name__


def _exception_text(exc: Exception) -> str:
    message = str(exc)
    if message:
        return message
    return exc.__class__.__name__


__all__ = [
    "CompactionController",
    "CompactionPreparation",
    "CompactionRequest",
    "CompactionSummarizer",
    "CompactionSummary",
    "DeterministicCompactionSummarizer",
    "maybe_summarize_compaction",
]
