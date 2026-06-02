"""Message Compaction - Compress conversation history for token optimization.

## Core Functions

1. estimateMessagesTokens() - Estimate total tokens in messages
2. splitMessagesByTokenShare() - Split messages by token share
3. chunkMessagesByMaxTokens() - Chunk by max tokens
4. computeAdaptiveChunkRatio() - Adaptive chunk ratio based on avg message size
5. summarizeChunks() - Summarize message chunks
6. summarizeWithFallback() - Fallback for oversized messages
7. summarizeInStages() - Multi-stage summarization
8. pruneHistoryForContextShare() - Prune history for context share
9. fixToolCallConsistency() - Fix tool_call/tool_response consistency after pruning

## Constants

- BASE_CHUNK_RATIO = 0.4
- MIN_CHUNK_RATIO = 0.15
- SAFETY_MARGIN = 1.2
- DEFAULT_PARTS = 2
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.utils.truncate import truncate
from src.runtime.context_summary import build_context_state_from_messages, build_structured_summary
from src.config import resolve_model_limits

logger = logging.getLogger(__name__)

# Constants for message compaction
BASE_CHUNK_RATIO = 0.4
MIN_CHUNK_RATIO = 0.15
SAFETY_MARGIN = 1.2  # 20% buffer for estimation inaccuracy
DEFAULT_SUMMARY_FALLBACK = "No prior history."
DEFAULT_PARTS = 2
MERGE_SUMMARIES_INSTRUCTIONS = (
    "Merge these partial summaries into a single cohesive summary. "
    "Preserve decisions, TODOs, open questions, and any constraints."
)


class AgentMessage:
    """Represents a message in the conversation."""
    
    def __init__(
        self,
        role: str = "user",
        content: str = "",
        timestamp: Optional[int] = None,
        tool_calls: Optional[List[Dict]] = None,
        tool_use_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ):
        self.role = role
        self.content = content
        self.timestamp = timestamp or int(__import__("time").time())
        self.tool_calls = tool_calls
        self.tool_use_id = tool_use_id
        self.tool_name = tool_name
    
    def to_dict(self) -> Dict[str, Any]:
        data = {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "tool_calls": self.tool_calls,
            "tool_use_id": self.tool_use_id,
        }
        if self.tool_name:
            data["tool_name"] = self.tool_name
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> "AgentMessage":
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            timestamp=data.get("timestamp"),
            tool_calls=data.get("tool_calls"),
            tool_use_id=data.get("tool_use_id"),
            tool_name=data.get("tool_name"),
        )
    
    def __repr__(self) -> str:
        return f"AgentMessage(role={self.role}, content={truncate(self.content, 50)}...)"


class CompactionStats:
    """Statistics from compaction operation."""
    
    def __init__(
        self,
        dropped_chunks: int = 0,
        dropped_messages: int = 0,
        dropped_tokens: int = 0,
        kept_tokens: int = 0,
        budget_tokens: int = 0,
        summary: Optional[str] = None,
    ):
        self.dropped_chunks = dropped_chunks
        self.dropped_messages = dropped_messages
        self.dropped_tokens = dropped_tokens
        self.kept_tokens = kept_tokens
        self.budget_tokens = budget_tokens
        self.summary = summary
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "dropped_chunks": self.dropped_chunks,
            "dropped_messages": self.dropped_messages,
            "dropped_tokens": self.dropped_tokens,
            "kept_tokens": self.kept_tokens,
            "budget_tokens": self.budget_tokens,
            "summary": self.summary,
        }


def estimate_tokens(text: str) -> int:
    """Estimate token count for text.
    
    Simple approximation: ~4 characters per token for English.
    """
    if not text:
        return 0
    # Handle non-string content (e.g., list from vision blocks)
    if not isinstance(text, str):
        text = str(text)
    # OpenAI's tiktoken is more accurate, but this is a simple approximation
    return len(text) // 4


def estimate_message_tokens(message: AgentMessage) -> int:
    """Estimate tokens in a message.
    
    Args:
        message: The message to estimate
        
    Returns:
        Estimated token count
    """
    tokens = 0
    
    # Count content
    if message.content:
        tokens += estimate_tokens(message.content)
    
    # Count role overhead (approximate)
    tokens += len(message.role) // 4 + 2
    
    # Count tool calls overhead
    if message.tool_calls:
        tokens += len(str(message.tool_calls)) // 4
    
    return int(tokens * SAFETY_MARGIN)


def estimate_messages_tokens(messages: List[AgentMessage]) -> int:
    """Estimate total tokens in messages.
    
    Args:
        messages: List of messages
        
    Returns:
        Total estimated tokens
    """
    return sum(estimate_message_tokens(msg) for msg in messages)


def normalize_parts(parts: int, message_count: int) -> int:
    """Normalize parts value based on message count.
    
    Args:
        parts: Requested parts
        message_count: Number of messages
        
    Returns:
        Normalized parts value
    """
    if not isinstance(parts, (int, float)) or parts <= 1:
        return 1
    return min(max(1, int(parts)), max(1, message_count))


def split_messages_by_token_share(
    messages: List[AgentMessage],
    parts: int = DEFAULT_PARTS,
) -> List[List[AgentMessage]]:
    """Split messages by token share.
    
    Args:
        messages: List of messages
        parts: Number of parts to split into
        
    Returns:
        List of message chunks
    """
    if not messages:
        return []
    
    normalized_parts = normalize_parts(parts, len(messages))
    if normalized_parts <= 1:
        return [messages]
    
    total_tokens = estimate_messages_tokens(messages)
    target_tokens = total_tokens / normalized_parts
    
    chunks: List[List[AgentMessage]] = []
    current: List[AgentMessage] = []
    current_tokens = 0
    
    for message in messages:
        message_tokens = estimate_message_tokens(message)
        
        if (
            len(chunks) < normalized_parts - 1 and
            current and
            current_tokens + message_tokens > target_tokens
        ):
            chunks.append(current)
            current = []
            current_tokens = 0
        
        current.append(message)
        current_tokens += message_tokens
    
    if current:
        chunks.append(current)
    
    return chunks


def chunk_messages_by_max_tokens(
    messages: List[AgentMessage],
    max_tokens: int,
) -> List[List[AgentMessage]]:
    """Chunk messages by maximum tokens.
    
    Args:
        messages: List of messages
        max_tokens: Maximum tokens per chunk
        
    Returns:
        List of message chunks
    """
    if not messages:
        return []
    
    chunks: List[List[AgentMessage]] = []
    current_chunk: List[AgentMessage] = []
    current_tokens = 0
    
    for message in messages:
        message_tokens = estimate_message_tokens(message)
        
        if current_chunk and current_tokens + message_tokens > max_tokens:
            chunks.append(current_chunk)
            current_chunk = []
            current_tokens = 0
        
        current_chunk.append(message)
        current_tokens += message_tokens
        
        # Split oversized messages
        if message_tokens > max_tokens:
            chunks.append(current_chunk)
            current_chunk = []
            current_tokens = 0
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks


def compute_adaptive_chunk_ratio(
    messages: List[AgentMessage],
    context_window: int,
) -> float:
    """Compute adaptive chunk ratio based on average message size.
    
    When messages are large, use smaller chunks to avoid exceeding model limits.
    
    Args:
        messages: List of messages
        context_window: Model's context window size
        
    Returns:
        Adaptive chunk ratio
    """
    if not messages:
        return BASE_CHUNK_RATIO
    
    total_tokens = estimate_messages_tokens(messages)
    avg_tokens = total_tokens / len(messages)
    
    # Apply safety margin
    safe_avg_tokens = avg_tokens * SAFETY_MARGIN
    avg_ratio = safe_avg_tokens / context_window
    
    # If average message is > 10% of context, reduce chunk ratio
    if avg_ratio > 0.1:
        reduction = min(avg_ratio * 2, BASE_CHUNK_RATIO - MIN_CHUNK_RATIO)
        return max(MIN_CHUNK_RATIO, BASE_CHUNK_RATIO - reduction)
    
    return BASE_CHUNK_RATIO


def is_oversized_for_summary(message: AgentMessage, context_window: int) -> bool:
    """Check if a message is too large to summarize.
    
    If single message > 50% of context, it can't be summarized safely.
    
    Args:
        message: Message to check
        context_window: Model's context window size
        
    Returns:
        True if message is oversized
    """
    tokens = estimate_message_tokens(message) * SAFETY_MARGIN
    return tokens > context_window * 0.5


def _parse_previous_summary(previous_summary: Optional[str]) -> Dict[str, Any]:
    """Light parser for prior structured summary lines."""
    if not previous_summary or not isinstance(previous_summary, str):
        return {}

    parsed: Dict[str, Any] = {}
    for raw_line in previous_summary.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        if line.startswith("- Objective:"):
            parsed["objective"] = line.split(":", 1)[1].strip()
        elif line.startswith("- Current state:"):
            parsed["current_state"] = line.split(":", 1)[1].strip()
        elif line.startswith("- Constraints:"):
            parsed["constraints"] = [part.strip() for part in line.split(":", 1)[1].split(";") if part.strip()]
        elif line.startswith("- Decisions:"):
            parsed["decisions"] = [part.strip() for part in line.split(":", 1)[1].split(";") if part.strip()]
        elif line.startswith("- Open loops:"):
            parsed["open_loops"] = [part.strip() for part in line.split(":", 1)[1].split(";") if part.strip()]
        elif line.startswith("- Next step:"):
            parsed["next_step"] = line.split(":", 1)[1].strip()
    return parsed


async def generate_summary(
    messages: List[AgentMessage],
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    instructions: Optional[str] = None,
    previous_summary: Optional[str] = None,
    max_tokens: int = 500,
) -> str:
    """Generate summary for messages using LLM.
    
    Args:
        messages: Messages to summarize
        model: LLM model to use
        api_key: API key for LLM
        instructions: Custom instructions for summary
        previous_summary: Previous summary to extend
        max_tokens: Maximum tokens for summary
        
    Returns:
        Generated summary
    """
    if not messages:
        return DEFAULT_SUMMARY_FALLBACK
    
    logger.info(f"Generating summary for {len(messages)} messages")

    prior_context_state = _parse_previous_summary(previous_summary)
    context_state = build_context_state_from_messages(
        messages,
        prior_context_state=prior_context_state,
        compaction_level="full",
        source_message_count=len(messages),
        recent_count=min(5, max(1, len(messages))),
    )
    summary = build_structured_summary(context_state)
    return summary or DEFAULT_SUMMARY_FALLBACK


async def summarize_chunks(
    messages: List[AgentMessage],
    summarize_func: Optional[Callable] = None,
    reserve_tokens: int = 1000,
    max_chunk_tokens: int = 4000,
    custom_instructions: Optional[str] = None,
    previous_summary: Optional[str] = None,
) -> str:
    """Summarize message chunks.
    
    Args:
        messages: Messages to summarize
        summarize_func: Custom summarize function
        reserve_tokens: Tokens to reserve
        max_chunk_tokens: Maximum tokens per chunk
        custom_instructions: Custom instructions
        previous_summary: Previous summary to extend
        
    Returns:
        Generated summary
    """
    if not messages:
        return previous_summary or DEFAULT_SUMMARY_FALLBACK
    
    chunks = chunk_messages_by_max_tokens(messages, max_chunk_tokens)
    summary = previous_summary
    
    for chunk in chunks:
        summary = await generate_summary(
            messages=chunk,
            instructions=custom_instructions,
            previous_summary=summary,
            max_tokens=reserve_tokens,
        )
    
    return summary or DEFAULT_SUMMARY_FALLBACK


async def summarize_with_fallback(
    messages: List[AgentMessage],
    summarize_func: Optional[Callable] = None,
    reserve_tokens: int = 1000,
    max_chunk_tokens: int = 4000,
    context_window: Optional[int] = None,
    custom_instructions: Optional[str] = None,
    previous_summary: Optional[str] = None,
) -> str:
    """Summarize with progressive fallback for oversized messages.
    
    If full summarization fails, tries partial summarization excluding oversized messages.
    
    Args:
        messages: Messages to summarize
        summarize_func: Custom summarize function
        reserve_tokens: Tokens to reserve
        max_chunk_tokens: Maximum tokens per chunk
        context_window: Model's context window
        custom_instructions: Custom instructions
        previous_summary: Previous summary to extend
        
    Returns:
        Generated summary
    """
    if not messages:
        return previous_summary or DEFAULT_SUMMARY_FALLBACK
    context_window = int(context_window or resolve_context_window_tokens(None))
    
    # Try full summarization first
    try:
        return await summarize_chunks(
            messages=messages,
            reserve_tokens=reserve_tokens,
            max_chunk_tokens=max_chunk_tokens,
            custom_instructions=custom_instructions,
            previous_summary=previous_summary,
        )
    except Exception as full_error:
        logger.warning(f"Full summarization failed: {full_error}")
    
    # Fallback: Summarize only small messages, note oversized ones
    small_messages: List[AgentMessage] = []
    oversized_notes: List[str] = []
    
    for msg in messages:
        if is_oversized_for_summary(msg, context_window):
            role = msg.role or "message"
            tokens = estimate_message_tokens(msg)
            oversized_notes.append(
                f"[Large {role} (~{tokens // 1000}K tokens) omitted from summary]"
            )
        else:
            small_messages.append(msg)
    
    if small_messages:
        try:
            partial_summary = await summarize_chunks(
                messages=small_messages,
                reserve_tokens=reserve_tokens,
                max_chunk_tokens=max_chunk_tokens,
                custom_instructions=custom_instructions,
                previous_summary=previous_summary,
            )
            notes = "\n\n" + "\n".join(oversized_notes) if oversized_notes else ""
            return partial_summary + notes
        except Exception as partial_error:
            logger.warning(f"Partial summarization also failed: {partial_error}")
    
    # Final fallback: Just note what was there
    return (
        f"Context contained {len(messages)} messages "
        f"({len(oversized_notes)} oversized). "
        "Summary unavailable due to size limits."
    )


async def summarize_in_stages(
    messages: List[AgentMessage],
    summarize_func: Optional[Callable] = None,
    reserve_tokens: int = 1000,
    max_chunk_tokens: int = 4000,
    context_window: Optional[int] = None,
    custom_instructions: Optional[str] = None,
    previous_summary: Optional[str] = None,
    parts: Optional[int] = None,
    min_messages_for_split: int = 4,
) -> str:
    """Summarize in multiple stages for large message sets.
    
    Args:
        messages: Messages to summarize
        summarize_func: Custom summarize function
        reserve_tokens: Tokens to reserve
        max_chunk_tokens: Maximum tokens per chunk
        context_window: Model's context window
        custom_instructions: Custom instructions
        previous_summary: Previous summary to extend
        parts: Number of parts to split
        min_messages_for_split: Minimum messages to consider splitting
        
    Returns:
        Generated summary
    """
    if not messages:
        return previous_summary or DEFAULT_SUMMARY_FALLBACK
    context_window = int(context_window or resolve_context_window_tokens(None))
    
    normalized_parts = normalize_parts(parts or DEFAULT_PARTS, len(messages))
    total_tokens = estimate_messages_tokens(messages)
    
    # If not enough to split, use simple summarization
    if (
        normalized_parts <= 1 or
        len(messages) < min_messages_for_split or
        total_tokens <= max_chunk_tokens
    ):
        return await summarize_with_fallback(
            messages=messages,
            reserve_tokens=reserve_tokens,
            max_chunk_tokens=max_chunk_tokens,
            context_window=context_window,
            custom_instructions=custom_instructions,
            previous_summary=previous_summary,
        )
    
    # Split and summarize each part
    splits = split_messages_by_token_share(messages, normalized_parts)
    splits = [chunk for chunk in splits if chunk]
    
    if len(splits) <= 1:
        return await summarize_with_fallback(
            messages=messages,
            reserve_tokens=reserve_tokens,
            max_chunk_tokens=max_chunk_tokens,
            context_window=context_window,
            custom_instructions=custom_instructions,
            previous_summary=previous_summary,
        )
    
    # Summarize each split
    partial_summaries: List[str] = []
    for chunk in splits:
        partial = await summarize_with_fallback(
            messages=chunk,
            reserve_tokens=reserve_tokens,
            max_chunk_tokens=max_chunk_tokens,
            context_window=context_window,
            previous_summary=previous_summary,
        )
        partial_summaries.append(partial)
    
    if len(partial_summaries) == 1:
        return partial_summaries[0]
    
    # Merge partial summaries
    merge_instructions = (
        custom_instructions +
        "\n\n" + MERGE_SUMMARIES_INSTRUCTIONS
        if custom_instructions
        else MERGE_SUMMARIES_INSTRUCTIONS
    )
    
    summary_messages = [
        AgentMessage(role="user", content=summary)
        for summary in partial_summaries
    ]
    
    return await summarize_with_fallback(
        messages=summary_messages,
        reserve_tokens=reserve_tokens,
        max_chunk_tokens=max_chunk_tokens,
        context_window=context_window,
        custom_instructions=merge_instructions,
    )


def fix_tool_call_consistency(messages: List[AgentMessage]) -> List[AgentMessage]:
    """Fix tool_call consistency after pruning.
    
    Ensures that if a tool response is dropped, the corresponding
    tool_call is also removed from the assistant message.
    Also removes orphaned tool responses if their assistant message was dropped.
    
    Args:
        messages: List of messages (may have inconsistent tool_calls)
        
    Returns:
        Messages with consistent tool_calls and tool responses
    """
    if not messages:
        return messages
    
    # First, collect ALL tool_call_ids from assistant messages (before filtering)
    # This is needed to identify orphaned tool responses
    all_assistant_tool_call_ids = set()
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("id"):
                    all_assistant_tool_call_ids.add(tc.get("id"))
    
    # Build a set of tool_call_ids that have tool responses
    tool_response_ids = set()
    for msg in messages:
        if msg.role == "tool" and msg.tool_use_id:
            tool_response_ids.add(msg.tool_use_id)
    
    # Fix messages - filter tool_calls and remove orphaned tool responses
    fixed = []
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            # Filter tool_calls to only include those that have responses
            valid_calls = [
                tc for tc in msg.tool_calls
                if tc.get("id") in tool_response_ids
            ]
            
            # Create new message with filtered tool_calls (don't mutate)
            fixed.append(AgentMessage(
                role=msg.role,
                content=msg.content,
                timestamp=msg.timestamp,
                tool_calls=valid_calls if valid_calls else None,
                tool_use_id=msg.tool_use_id,
                tool_name=getattr(msg, "tool_name", None),
            ))
        elif msg.role == "tool":
            # Keep only tool responses whose tool_use_id appears in some assistant.tool_calls
            if msg.tool_use_id in all_assistant_tool_call_ids:
                fixed.append(msg)
            # else: orphaned tool response (assistant message was dropped), skip it
        else:
            fixed.append(msg)
    
    return fixed


def prune_history_for_context_share(
    messages: List[AgentMessage],
    max_context_tokens: int,
    max_history_share: float = 0.5,
    parts: Optional[int] = None,
) -> Tuple[List[AgentMessage], CompactionStats]:
    """Prune history to fit within context share budget.
    
    Args:
        messages: All messages
        max_context_tokens: Maximum context tokens
        max_history_share: Fraction of context for history (default: 0.5)
        parts: Number of parts for splitting
        
    Returns:
        Tuple of (pruned_messages, stats)
    """
    budget_tokens = max(1, int(max_context_tokens * max_history_share))
    kept_messages = messages
    all_dropped: List[AgentMessage] = []
    dropped_chunks = 0
    dropped_messages_count = 0
    dropped_tokens = 0
    
    normalized_parts = normalize_parts(parts or DEFAULT_PARTS, len(kept_messages))
    
    while kept_messages and estimate_messages_tokens(kept_messages) > budget_tokens:
        chunks = split_messages_by_token_share(kept_messages, normalized_parts)
        
        if len(chunks) <= 1:
            break
        
        # Drop the oldest chunk
        dropped, rest = chunks[0], chunks[1:]
        kept_messages = [msg for chunk in rest for msg in chunk]
        
        dropped_chunks += 1
        dropped_messages_count += len(dropped)
        dropped_tokens += estimate_messages_tokens(dropped)
        all_dropped.extend(dropped)
    
    return (
        kept_messages,
        CompactionStats(
            dropped_chunks=dropped_chunks,
            dropped_messages=dropped_messages_count,
            dropped_tokens=dropped_tokens,
            kept_tokens=estimate_messages_tokens(kept_messages),
            budget_tokens=budget_tokens,
        ),
    )


async def compact_messages(
    messages: List[AgentMessage],
    max_tokens: int,
    summarize_func: Optional[Callable] = None,
    context_window: Optional[int] = None,
    recent_count: int = 3,
) -> Tuple[List[AgentMessage], CompactionStats]:
    """Compact messages for token optimization.
    
    Main entry point for message compaction.
    
    Args:
        messages: All messages to compact
        max_tokens: Maximum tokens allowed
        summarize_func: Custom summarize function
        context_window: Model's context window
        recent_count: Number of recent messages to keep
        
    Returns:
        Tuple of (compacted_messages, stats)
    """
    if not messages:
        return [], CompactionStats()
    context_window = int(context_window or resolve_context_window_tokens(None))
    
    # Estimate current tokens
    current_tokens = estimate_messages_tokens(messages)
    
    if current_tokens <= max_tokens:
        return messages, CompactionStats(
            kept_tokens=current_tokens,
            budget_tokens=max_tokens,
            summary="No compaction needed",
        )
    
    # Calculate budget for history (half of max_tokens)
    history_budget = max_tokens // 2
    
    # Prune old messages
    pruned, stats = prune_history_for_context_share(
        messages=messages,
        max_context_tokens=max_tokens,
        max_history_share=0.5,
    )
    
    # Fix tool_call consistency after pruning
    # Ensures tool_calls and tool responses are paired
    pruned = fix_tool_call_consistency(pruned)
    
    # If still over budget, summarize old messages
    if estimate_messages_tokens(pruned) > history_budget:
        # Keep recent messages
        recent = pruned[-recent_count:]
        old = pruned[:-recent_count]
        
        # Summarize old messages
        summary = await summarize_in_stages(
            messages=old,
            max_chunk_tokens=history_budget,
            context_window=context_window,
        )
        
        # Create summary message
        summary_message = AgentMessage(
            role="system",
            content=f"Summary of earlier conversation:\n\n{summary}",
        )
        
        result = [summary_message] + recent
        # Fix tool_call consistency for summarized messages
        result = fix_tool_call_consistency(result)
        stats.summary = summary
        
        return result, stats
    
    # Final fix before returning
    return pruned, stats


# Convenience functions
def resolve_context_window_tokens(model: Optional[str] = None) -> int:
    """Resolve context window tokens for a model.

    Explicit model names use deterministic supported-model mapping so unknown
    strings do not inherit the configured default model's large fallback
    window. ``model=None`` keeps configured-runtime behavior.
    """
    context_windows = {
        "gpt-5-mini": 264000,
        "gpt-5.3-codex": 400000,
        "gpt-5.4": 400000,
        "gpt-5.4-mini": 400000,
        "gpt-5.5": 400000,
        "gemini-2.5-pro": 128000,
        "gemini-3.5-flash": 128000,
    }

    if model is None:
        limits = resolve_model_limits(None)
        if limits.get("max_context_window_tokens"):
            return int(limits["max_context_window_tokens"])
        return 4096

    model_lower = "-".join(str(model).lower().split())
    model_name = model_lower.split("/", 1)[1] if "/" in model_lower else model_lower
    return context_windows.get(model_name, 4096)


def normalize_compaction_threshold(raw_value, default_value=0.8):
    """Normalize compaction threshold to float in (0, 1).
    
    Supports:
    - numeric values like 0.8
    - percent-style values like 80 (interpreted as 80%)
    - string representations of the above
    
    Args:
        raw_value: The value from config
        default_value: Fallback if invalid
        
    Returns:
        Float in range [0.1, 0.95]
    """
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default_value
    
    # Interpret values >= 2 as percentages (e.g., 80 -> 0.8)
    # Values like 1.5 are treated as-is (will be clamped to 0.95)
    if value >= 2:
        value = value / 100.0
    
    # Clamp to sensible range [0.1, 0.95]
    clamped = max(0.1, min(0.95, value))
    
    return clamped


# Export
__all__ = [
    "AgentMessage",
    "CompactionStats",
    "estimate_tokens",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "split_messages_by_token_share",
    "chunk_messages_by_max_tokens",
    "compute_adaptive_chunk_ratio",
    "is_oversized_for_summary",
    "generate_summary",
    "summarize_chunks",
    "summarize_with_fallback",
    "summarize_in_stages",
    "prune_history_for_context_share",
    "compact_messages",
    "fix_tool_call_consistency",
    "resolve_context_window_tokens",
    "normalize_compaction_threshold",
    # Constants
    "BASE_CHUNK_RATIO",
    "MIN_CHUNK_RATIO",
    "SAFETY_MARGIN",
    "DEFAULT_PARTS",
    "MERGE_SUMMARIES_INSTRUCTIONS",
]
