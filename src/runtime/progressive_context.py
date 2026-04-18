"""Progressive context preparation and durable context state helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Tuple

from src.agents.compaction import (
    AgentMessage,
    compact_messages,
    estimate_messages_tokens,
    fix_tool_call_consistency,
    normalize_compaction_threshold,
    resolve_context_window_tokens,
)
from src.config import config
from src.sessions.manager import session_manager
from src.utils.truncate import truncate


_PREVIEW_LIMIT = 240
_SUMMARY_LIMIT = 320
_NEXT_STEP_FALLBACK = "Continue from the latest state and preserve prior constraints."
_CONSTRAINT_KEYWORDS = ("must", "should", "need to", "do not", "don't", "不要", "不能", "必须")


@dataclass
class ProgressivePreparation:
    messages: List[Dict[str, Any]]
    context_state: Dict[str, Any]
    history_changed: bool
    full_summary: Optional[str] = None


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


def _to_dict_messages(messages: List[AgentMessage]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for msg in messages:
        item: Dict[str, Any] = {
            "role": msg.role,
            "content": msg.content,
        }
        if msg.timestamp:
            item["timestamp"] = msg.timestamp
        if msg.tool_calls:
            item["tool_calls"] = msg.tool_calls
        if msg.tool_use_id:
            item["tool_call_id"] = msg.tool_use_id
        result.append(item)
    return result


def _safe_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
        return " ".join(parts).strip()
    return str(value or "").strip()


def _truncate(value: str, limit: int) -> str:
    return truncate(value, limit) if value else ""


def _micro_compact_messages(messages: List[AgentMessage], *, recent_count: int) -> List[AgentMessage]:
    if not messages:
        return messages

    keep_message_start = max(0, len(messages) - max(1, recent_count))
    tool_indices = [idx for idx, msg in enumerate(messages) if msg.role == "tool"]
    keep_tool_indices = set(tool_indices[-4:])

    compacted: List[AgentMessage] = []
    for idx, msg in enumerate(messages):
        if idx >= keep_message_start:
            compacted.append(msg)
            continue

        if msg.role == "tool":
            content_text = _safe_text(msg.content)
            if idx not in keep_tool_indices and len(content_text) > 800:
                preview = _truncate(content_text, 240)
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
            content_text = _safe_text(msg.content)
            if len(content_text) > 1200:
                compacted.append(
                    AgentMessage(
                        role="assistant",
                        content=_truncate(content_text, 320),
                        timestamp=msg.timestamp,
                        tool_calls=msg.tool_calls,
                        tool_use_id=msg.tool_use_id,
                    )
                )
                continue

        compacted.append(msg)

    return fix_tool_call_consistency(compacted)


def _extract_objective(messages: List[AgentMessage]) -> str:
    user_texts = [_safe_text(m.content) for m in messages if m.role == "user" and _safe_text(m.content)]
    if not user_texts:
        return ""
    return _truncate(user_texts[0] or user_texts[-1], _PREVIEW_LIMIT)


def _extract_constraints(messages: List[AgentMessage]) -> List[str]:
    constraints: List[str] = []
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        text = _safe_text(msg.content)
        if not text:
            continue
        for sentence in re.split(r"[\n\.。!！?？]", text):
            candidate = sentence.strip()
            if not candidate:
                continue
            lower = candidate.lower()
            if any(keyword in lower or keyword in candidate for keyword in _CONSTRAINT_KEYWORDS):
                normalized = _truncate(candidate, 140)
                if normalized and normalized not in constraints:
                    constraints.append(normalized)
                    if len(constraints) >= 5:
                        return constraints
    return constraints


def _extract_latest_assistant_preview(messages: List[AgentMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "assistant":
            text = _safe_text(msg.content)
            if text:
                return _truncate(text, 160)
    return ""


def _extract_next_step(messages: List[AgentMessage]) -> str:
    assistant_text = _extract_latest_assistant_preview(messages)
    if assistant_text:
        lowered = assistant_text.lower()
        if any(token in lowered for token in ("next", "continue", "please", "run", "check", "verify", "then")):
            return _truncate(assistant_text, 180)
    return _NEXT_STEP_FALLBACK


def _build_summary(messages: List[AgentMessage], *, objective: str, full_summary: Optional[str]) -> str:
    if full_summary:
        return _truncate(full_summary, _SUMMARY_LIMIT)
    assistant_preview = _extract_latest_assistant_preview(messages)
    tool_count = sum(1 for msg in messages if msg.role == "tool")
    parts = [part for part in [objective, assistant_preview] if part]
    if tool_count:
        parts.append(f"Tool interactions: {tool_count}.")
    if not parts:
        return "Conversation context state updated."
    return _truncate(" | ".join(parts), _SUMMARY_LIMIT)


def _build_context_state(
    *,
    source_messages: List[AgentMessage],
    prepared_messages: List[AgentMessage],
    compaction_level: str,
    recent_count: int,
    full_summary: Optional[str] = None,
) -> Dict[str, Any]:
    objective = _extract_objective(prepared_messages or source_messages)
    summary = _build_summary(prepared_messages, objective=objective, full_summary=full_summary)
    return {
        "version": "context.v1",
        "compaction_level": compaction_level,
        "objective": objective,
        "summary": summary,
        "next_step": _extract_next_step(prepared_messages),
        "constraints": _extract_constraints(prepared_messages),
        "source_message_count": len(source_messages),
        "recent_window_count": recent_count,
        "last_compacted_at": datetime.now(timezone.utc).isoformat() if compaction_level in {"micro", "full"} else None,
    }


def build_portal_context_preview(context_state: dict | None) -> dict:
    if not isinstance(context_state, dict):
        return {}
    preview = {
        "context_compaction_level": context_state.get("compaction_level"),
        "context_objective_preview": _truncate(str(context_state.get("objective") or ""), 140),
        "context_summary_preview": _truncate(str(context_state.get("summary") or ""), 180),
        "context_next_step_preview": _truncate(str(context_state.get("next_step") or ""), 140),
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
    del session_id, stage
    source_messages = _to_agent_messages(messages)
    if not source_messages:
        context_state = _build_context_state(
            source_messages=[],
            prepared_messages=[],
            compaction_level="none",
            recent_count=recent_count,
        )
        return [], context_state

    context_window = resolve_context_window_tokens(model)
    soft_threshold = int(context_window * 0.65)
    configured_threshold = normalize_compaction_threshold(config.llm.get("compaction_threshold", 0.8), 0.8)
    hard_threshold = int(context_window * configured_threshold)
    current_tokens = estimate_messages_tokens(source_messages)

    if current_tokens <= soft_threshold:
        context_state = _build_context_state(
            source_messages=source_messages,
            prepared_messages=source_messages,
            compaction_level="none",
            recent_count=recent_count,
        )
        return list(messages), context_state

    micro_messages = _micro_compact_messages(source_messages, recent_count=recent_count)
    micro_tokens = estimate_messages_tokens(micro_messages)
    if micro_tokens <= hard_threshold:
        context_state = _build_context_state(
            source_messages=source_messages,
            prepared_messages=micro_messages,
            compaction_level="micro",
            recent_count=recent_count,
        )
        return _to_dict_messages(micro_messages), context_state

    full_messages, stats = await compact_messages(
        messages=micro_messages,
        max_tokens=hard_threshold,
        context_window=context_window,
        recent_count=recent_count,
    )
    context_state = _build_context_state(
        source_messages=source_messages,
        prepared_messages=full_messages,
        compaction_level="full",
        recent_count=recent_count,
        full_summary=stats.summary,
    )
    return _to_dict_messages(full_messages), context_state


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

    metadata = session.setdefault("metadata", {})
    metadata["context_state"] = context_state
    preview = build_portal_context_preview(context_state)
    metadata.update(preview)

    summary = context_state.get("summary")
    if summary:
        metadata["session_memory_summary"] = summary
    if context_state.get("compaction_level") == "full" and summary:
        metadata["compaction_summary"] = summary

    session["updated_at"] = datetime.now().isoformat()

    if session_manager.persistence_enabled:
        session_manager._schedule_metadata_persist(session_id, session)

    return context_state
