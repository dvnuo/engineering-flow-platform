"""Runtime v2 compaction strategies."""

from .controller import (
    CompactionController,
    CompactionPreparation,
    CompactionRequest,
    CompactionSummarizer,
    CompactionSummary,
    DeterministicCompactionSummarizer,
    maybe_summarize_compaction,
)
from .summary import (
    COMPACTION_SUMMARY_HEADINGS,
    build_compaction_prompt,
    latest_compaction_summary,
    render_anchored_compaction_summary,
)
from .strategy import (
    BudgetCompactionStrategy,
    CompactionResult,
    ContextBudget,
    PartAwareCompactionStrategy,
    TailTurnCompactionStrategy,
)
from .prune import ToolOutputPruneResult, prune_old_tool_outputs

__all__ = [
    "BudgetCompactionStrategy",
    "COMPACTION_SUMMARY_HEADINGS",
    "CompactionController",
    "CompactionPreparation",
    "CompactionRequest",
    "CompactionResult",
    "CompactionSummarizer",
    "CompactionSummary",
    "ContextBudget",
    "DeterministicCompactionSummarizer",
    "PartAwareCompactionStrategy",
    "TailTurnCompactionStrategy",
    "ToolOutputPruneResult",
    "build_compaction_prompt",
    "latest_compaction_summary",
    "maybe_summarize_compaction",
    "prune_old_tool_outputs",
    "render_anchored_compaction_summary",
]
