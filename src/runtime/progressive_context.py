"""Progressive context preparation and durable context state helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Sequence, Set

from src.agents.compaction import (
    AgentMessage,
    compact_messages,
    estimate_messages_tokens,
    fix_tool_call_consistency,
    normalize_compaction_threshold,
    resolve_context_window_tokens,
)
from src.config import config
from src.runtime.context_summary import (
    build_context_state_from_messages,
    build_recovery_context_message,
)
from src.sessions.manager import session_manager
from src.utils.truncate import truncate

logger = logging.getLogger(__name__)


def _to_agent_messages(messages: List[Dict[str, Any]]) -> List[AgentMessage]:
    return [
        AgentMessage(
            role=str(msg.get("role") or "user"),
            content=msg.get("content", ""),
            timestamp=msg.get("timestamp"),
            tool_calls=msg.get("tool_calls"),
            tool_use_id=msg.get("tool_call_id") or msg.get("tool_use_id"),
        )
        for msg in messages
        if isinstance(msg, dict)
    ]


def _to_dict_messages(messages: Sequence[AgentMessage]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for msg in messages:
        item: Dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.timestamp:
            item["timestamp"] = msg.timestamp
        if msg.tool_calls:
            item["tool_calls"] = msg.tool_calls
        if msg.tool_use_id:
            item["tool_call_id"] = msg.tool_use_id
        result.append(item)
    return result


def _collect_protected_tool_chain_ids(messages: Sequence[AgentMessage], recent_count: int) -> Set[str]:
    protected: Set[str] = set()
    if not messages:
        return protected

    tail = list(messages[-max(1, recent_count + 2):])
    for msg in tail:
        if msg.role == "tool" and msg.tool_use_id:
            protected.add(msg.tool_use_id)
    for msg in reversed(tail):
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        for call in msg.tool_calls:
            call_id = str(call.get("id") or "").strip()
            if call_id:
                protected.add(call_id)
        if protected:
            break
    return protected


def _micro_compact_messages(messages: List[AgentMessage], *, recent_count: int) -> List[AgentMessage]:
    if not messages:
        return messages

    keep_message_start = max(0, len(messages) - max(1, recent_count))
    tool_indices = [idx for idx, msg in enumerate(messages) if msg.role == "tool"]
    keep_tool_indices = set(tool_indices[-4:])
    protected_tool_chain_ids = _collect_protected_tool_chain_ids(messages, recent_count)

    compacted: List[AgentMessage] = []
    for idx, msg in enumerate(messages):
        if idx >= keep_message_start:
            compacted.append(msg)
            continue

        if msg.role == "tool":
            content_text = str(msg.content or "").strip()
            if msg.tool_use_id and msg.tool_use_id in protected_tool_chain_ids:
                compacted.append(msg)
                continue
            if idx not in keep_tool_indices and len(content_text) > 800:
                preview = truncate(content_text, 240)
                compacted.append(
                    AgentMessage(
                        role="tool",
                        content=f"[tool_result compacted | original_chars={len(content_text)}] {preview}",
                        timestamp=msg.timestamp,
                        tool_use_id=msg.tool_use_id,
                    )
                )
                continue
            compacted.append(msg)
            continue

        if msg.role == "assistant" and msg.tool_calls:
            call_ids = {str(tc.get("id") or "").strip() for tc in msg.tool_calls if tc.get("id")}
            if call_ids.intersection(protected_tool_chain_ids):
                compacted.append(msg)
                continue
            content_text = str(msg.content or "").strip()
            if len(content_text) > 1200:
                compacted.append(
                    AgentMessage(
                        role="assistant",
                        content=truncate(content_text, 320),
                        timestamp=msg.timestamp,
                        tool_calls=msg.tool_calls,
                        tool_use_id=msg.tool_use_id,
                    )
                )
                continue

        compacted.append(msg)

    return fix_tool_call_consistency(compacted)


def _resolve_stage_thresholds(*, stage: str, context_window: int, hard_threshold: int) -> tuple[int, int]:
    if stage == "tool_loop":
        soft = int(context_window * 0.60)
        hard = min(hard_threshold, int(context_window * 0.75))
        return soft, max(soft + 1, hard)
    return int(context_window * 0.65), hard_threshold


def _build_synthetic_summary_message(context_state: Dict[str, Any]) -> AgentMessage:
    return AgentMessage(
        role="system",
        content=str(context_state.get("summary") or "Context summary:"),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _select_recent_window_for_full(messages: Sequence[AgentMessage], recent_count: int) -> List[AgentMessage]:
    if not messages:
        return []
    start = max(0, len(messages) - max(1, recent_count))
    selected = list(messages[start:])
    required_tool_ids = {msg.tool_use_id for msg in selected if msg.role == "tool" and msg.tool_use_id}
    if required_tool_ids:
        for msg in messages[:start]:
            if msg.role != "assistant" or not msg.tool_calls:
                continue
            call_ids = {str(tc.get("id") or "").strip() for tc in msg.tool_calls if tc.get("id")}
            if call_ids.intersection(required_tool_ids):
                selected.insert(0, msg)
                break
    return fix_tool_call_consistency(selected)


def _trim_full_compaction_result_to_budget(
    *,
    summary_message: AgentMessage,
    recent_window: Sequence[AgentMessage],
    hard_threshold: int,
) -> List[AgentMessage]:
    trimmed_recent_window = list(recent_window)
    candidate = fix_tool_call_consistency([summary_message] + trimmed_recent_window)
    if estimate_messages_tokens(candidate) <= hard_threshold:
        return candidate

    while trimmed_recent_window:
        trimmed_recent_window = trimmed_recent_window[1:]
        candidate = fix_tool_call_consistency([summary_message] + trimmed_recent_window)
        if estimate_messages_tokens(candidate) <= hard_threshold:
            return candidate

    shortened_summary = AgentMessage(
        role=summary_message.role,
        content=truncate(str(summary_message.content or ""), 220),
        timestamp=summary_message.timestamp,
        tool_calls=summary_message.tool_calls,
        tool_use_id=summary_message.tool_use_id,
    )
    return fix_tool_call_consistency([shortened_summary])


def _annotate_context_state(
    context_state: Dict[str, Any],
    *,
    summary_source: str,
    history_from_count: int,
    history_to_count: int,
) -> Dict[str, Any]:
    annotated = dict(context_state)
    annotated["summary_source"] = summary_source
    annotated["history_compacted_from_count"] = history_from_count
    annotated["history_compacted_to_count"] = history_to_count
    annotated["recovery_context_message"] = build_recovery_context_message(annotated)
    return annotated


def build_portal_context_preview(context_state: dict | None) -> dict:
    if not isinstance(context_state, dict):
        return {}
    preview = {
        "context_compaction_level": context_state.get("compaction_level"),
        "context_objective_preview": truncate(str(context_state.get("objective") or ""), 140),
        "context_summary_preview": truncate(str(context_state.get("summary") or ""), 180),
        "context_next_step_preview": truncate(str(context_state.get("next_step") or ""), 140),
    }
    return {key: value for key, value in preview.items() if value not in (None, "")}


async def prepare_progressive_messages(
    *,
    messages: list[dict],
    model: str | None,
    session_id: str,
    stage: str,
    recent_count: int = 5,
) -> tuple[list[dict], dict]:
    prior_context_state: Dict[str, Any] = {}
    if session_id:
        try:
            prior = await session_manager.get_context_state(session_id)
            if isinstance(prior, dict):
                prior_context_state = dict(prior)
        except Exception:
            logger.warning("Failed to load prior context_state", exc_info=True)

    source_messages = _to_agent_messages(messages)
    source_count = len(source_messages)

    if not source_messages:
        state = build_context_state_from_messages(
            source_messages,
            prior_context_state=prior_context_state,
            compaction_level="none",
            source_message_count=0,
            recent_count=recent_count,
        )
        return [], _annotate_context_state(state, summary_source="none", history_from_count=0, history_to_count=0)

    context_window = resolve_context_window_tokens(model)
    configured_threshold = normalize_compaction_threshold(config.llm.get("compaction_threshold", 0.8), 0.8)
    soft_threshold, hard_threshold = _resolve_stage_thresholds(
        stage=stage,
        context_window=context_window,
        hard_threshold=int(context_window * configured_threshold),
    )

    current_tokens = estimate_messages_tokens(source_messages)
    compaction_level = "none"
    prepared_messages = list(source_messages)
    summary_source = "none"

    if current_tokens > soft_threshold:
        micro_messages = _micro_compact_messages(source_messages, recent_count=recent_count)
        prepared_messages = micro_messages
        compaction_level = "micro"
        summary_source = "micro"

        micro_tokens = estimate_messages_tokens(micro_messages)
        if micro_tokens > hard_threshold:
            full_messages, _stats = await compact_messages(
                messages=micro_messages,
                max_tokens=hard_threshold,
                context_window=context_window,
                recent_count=recent_count,
            )
            compaction_level = "full"
            summary_source = "full"

            source_state = build_context_state_from_messages(
                source_messages,
                prior_context_state=prior_context_state,
                compaction_level=compaction_level,
                source_message_count=source_count,
                recent_count=recent_count,
            )
            recent_window = _select_recent_window_for_full(full_messages, recent_count)
            synthetic_summary_message = _build_synthetic_summary_message(source_state)
            prepared_messages = _trim_full_compaction_result_to_budget(
                summary_message=synthetic_summary_message,
                recent_window=recent_window,
                hard_threshold=hard_threshold,
            )

    if compaction_level == "full":
        merged_state = build_context_state_from_messages(
            source_messages,
            prior_context_state=prior_context_state,
            compaction_level=compaction_level,
            source_message_count=source_count,
            recent_count=recent_count,
        )
    else:
        merged_state = build_context_state_from_messages(
            prepared_messages,
            prior_context_state=prior_context_state,
            compaction_level=compaction_level,
            source_message_count=source_count,
            recent_count=recent_count,
        )

    if stage == "post_turn":
        merged_state = build_context_state_from_messages(
            source_messages,
            prior_context_state=merged_state,
            compaction_level=compaction_level,
            source_message_count=source_count,
            recent_count=recent_count,
        )

    final_messages = list(messages) if compaction_level == "none" else _to_dict_messages(prepared_messages)
    annotated_state = _annotate_context_state(
        merged_state,
        summary_source=summary_source,
        history_from_count=source_count,
        history_to_count=len(final_messages),
    )
    return final_messages, annotated_state


async def apply_progressive_context_after_turn(
    *,
    session_id: str,
    model: str | None,
) -> dict:
    if not session_id:
        return {}

    session = await session_manager.get_session(session_id)
    history = list(session.get("history") or [])
    prepared_messages, context_state = await prepare_progressive_messages(
        messages=history,
        model=model,
        session_id=session_id,
        stage="post_turn",
        recent_count=5,
    )

    if prepared_messages != history:
        session["history"] = prepared_messages
        session["updated_at"] = datetime.now().isoformat()

    await session_manager.set_context_state(session_id, context_state)
    return context_state
