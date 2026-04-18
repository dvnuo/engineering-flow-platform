"""Progressive context preparation and durable context state helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Set

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

logger = logging.getLogger(__name__)

_PREVIEW_LIMIT = 240
_SUMMARY_LIMIT = 320
_NEXT_STEP_FALLBACK = "Continue from the latest state and preserve prior constraints."
_CONSTRAINT_KEYWORDS = ("must", "should", "need to", "do not", "don't", "不要", "不能", "必须")
_DECISION_HINTS = ("decide", "decided", "decision", "we will", "chosen", "选择", "决定", "采用")
_OPEN_LOOP_HINTS = ("todo", "follow up", "pending", "next", "blocker", "wait", "待办", "后续", "未完成", "需要")


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
            content_text = _safe_text(msg.content)
            if msg.tool_use_id and msg.tool_use_id in protected_tool_chain_ids:
                compacted.append(msg)
                continue
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
            call_ids = {str(tc.get("id") or "").strip() for tc in msg.tool_calls if tc.get("id")}
            if call_ids.intersection(protected_tool_chain_ids):
                compacted.append(msg)
                continue
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


def _extract_objective(messages: Sequence[AgentMessage]) -> str:
    first_user = ""
    latest_user = ""
    for msg in messages:
        if msg.role != "user":
            continue
        text = _safe_text(msg.content)
        if not text:
            continue
        if not first_user:
            first_user = text
        latest_user = text
    return _truncate(first_user or latest_user, _PREVIEW_LIMIT)


def _extract_constraints(messages: Sequence[AgentMessage]) -> List[str]:
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


def _extract_current_state(messages: Sequence[AgentMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "assistant":
            text = _safe_text(msg.content)
            if text:
                return _truncate(text, 220)
    for msg in reversed(messages):
        if msg.role == "tool":
            text = _safe_text(msg.content)
            if text:
                return _truncate(f"Latest tool result: {text}", 220)
    return ""


def _extract_next_step(messages: Sequence[AgentMessage]) -> str:
    for msg in reversed(messages):
        if msg.role != "assistant":
            continue
        assistant_text = _safe_text(msg.content)
        if not assistant_text:
            continue
        lowered = assistant_text.lower()
        if any(token in lowered for token in ("next", "continue", "please", "run", "check", "verify", "then", "should")):
            return _truncate(assistant_text, 180)
    return _NEXT_STEP_FALLBACK


def _extract_decisions(messages: Sequence[AgentMessage]) -> List[str]:
    decisions: List[str] = []
    for msg in reversed(messages):
        if msg.role not in {"assistant", "user"}:
            continue
        text = _safe_text(msg.content)
        if not text:
            continue
        for sentence in re.split(r"[\n\.。!！?？]", text):
            candidate = sentence.strip()
            if not candidate:
                continue
            lowered = candidate.lower()
            if any(hint in lowered or hint in candidate for hint in _DECISION_HINTS):
                normalized = _truncate(candidate, 140)
                if normalized and normalized not in decisions:
                    decisions.append(normalized)
                    if len(decisions) >= 5:
                        return decisions
    return decisions


def _extract_open_loops(messages: Sequence[AgentMessage]) -> List[str]:
    open_loops: List[str] = []
    for msg in reversed(messages):
        if msg.role not in {"assistant", "user"}:
            continue
        text = _safe_text(msg.content)
        if not text:
            continue
        for sentence in re.split(r"[\n\.。!！?？]", text):
            candidate = sentence.strip()
            if not candidate:
                continue
            lowered = candidate.lower()
            if "?" in candidate or any(hint in lowered or hint in candidate for hint in _OPEN_LOOP_HINTS):
                normalized = _truncate(candidate, 140)
                if normalized and normalized not in open_loops:
                    open_loops.append(normalized)
                    if len(open_loops) >= 5:
                        return open_loops
    return open_loops


def _is_weaker_objective(current: str, prior: str) -> bool:
    if not prior:
        return False
    if not current:
        return True
    if len(current.strip()) < 12 <= len(prior.strip()):
        return True
    if len(current.strip()) * 2 < len(prior.strip()):
        return True
    return False


def _merge_unique(prior_values: Any, current_values: Any, *, limit: int = 5) -> List[str]:
    merged: List[str] = []
    for raw in list(prior_values or []) + list(current_values or []):
        text = _truncate(str(raw or "").strip(), 160)
        if text and text not in merged:
            merged.append(text)
            if len(merged) >= limit:
                break
    return merged


def _build_structured_summary(context_state: Dict[str, Any]) -> str:
    lines = ["Context summary:"]
    objective = _truncate(str(context_state.get("objective") or "").strip(), 200)
    current_state = _truncate(str(context_state.get("current_state") or "").strip(), 220)
    constraints = "; ".join(_merge_unique([], context_state.get("constraints"), limit=5))
    decisions = "; ".join(_merge_unique([], context_state.get("decisions"), limit=5))
    open_loops = "; ".join(_merge_unique([], context_state.get("open_loops"), limit=5))
    next_step = _truncate(str(context_state.get("next_step") or "").strip(), 180)

    if objective:
        lines.append(f"- Objective: {objective}")
    if current_state:
        lines.append(f"- Current state: {current_state}")
    if constraints:
        lines.append(f"- Constraints: {constraints}")
    if decisions:
        lines.append(f"- Decisions: {decisions}")
    if open_loops:
        lines.append(f"- Open loops: {open_loops}")
    if next_step:
        lines.append(f"- Next step: {next_step}")

    if len(lines) == 1:
        lines.append("- Current state: Context state refreshed.")
    return _truncate("\n".join(lines), _SUMMARY_LIMIT)


def _merge_prior_and_current_context_state(
    *,
    prior_context_state: Dict[str, Any],
    current_context_state: Dict[str, Any],
    compaction_level: str,
    source_message_count: int,
    recent_count: int,
) -> Dict[str, Any]:
    prior_objective = _truncate(str(prior_context_state.get("objective") or "").strip(), _PREVIEW_LIMIT)
    current_objective = _truncate(str(current_context_state.get("objective") or "").strip(), _PREVIEW_LIMIT)
    objective = prior_objective if _is_weaker_objective(current_objective, prior_objective) else current_objective

    next_step = _truncate(str(current_context_state.get("next_step") or "").strip(), 180)
    if not next_step or next_step == _NEXT_STEP_FALLBACK:
        prior_next = _truncate(str(prior_context_state.get("next_step") or "").strip(), 180)
        next_step = prior_next or _NEXT_STEP_FALLBACK

    merged: Dict[str, Any] = {
        "version": "context.v1",
        "compaction_level": compaction_level,
        "objective": objective,
        "current_state": _truncate(
            str(current_context_state.get("current_state") or prior_context_state.get("current_state") or "").strip(),
            220,
        ),
        "next_step": next_step,
        "constraints": _merge_unique(prior_context_state.get("constraints"), current_context_state.get("constraints")),
        "decisions": _merge_unique(prior_context_state.get("decisions"), current_context_state.get("decisions")),
        "open_loops": _merge_unique(prior_context_state.get("open_loops"), current_context_state.get("open_loops")),
        "source_message_count": source_message_count,
        "recent_window_count": recent_count,
        "last_compacted_at": datetime.now(timezone.utc).isoformat() if compaction_level in {"micro", "full"} else None,
    }
    merged["summary"] = _build_structured_summary(merged)
    return merged


def _build_current_context_state(messages: Sequence[AgentMessage]) -> Dict[str, Any]:
    return {
        "objective": _extract_objective(messages),
        "current_state": _extract_current_state(messages),
        "next_step": _extract_next_step(messages),
        "constraints": _extract_constraints(messages),
        "decisions": _extract_decisions(messages),
        "open_loops": _extract_open_loops(messages),
    }


def _resolve_stage_thresholds(*, stage: str, context_window: int, hard_threshold: int) -> tuple[int, int]:
    if stage == "tool_loop":
        soft = int(context_window * 0.60)
        hard = min(hard_threshold, int(context_window * 0.75))
        return soft, max(soft + 1, hard)
    return int(context_window * 0.65), hard_threshold


def _build_synthetic_summary_message(context_state: Dict[str, Any]) -> AgentMessage:
    return AgentMessage(
        role="system",
        content=_build_structured_summary(context_state),
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
    prior_context_state: Dict[str, Any] = {}
    if session_id:
        try:
            prior = await session_manager.get_context_state(session_id)
            if isinstance(prior, dict):
                prior_context_state = dict(prior)
        except Exception:
            logger.warning("Failed to load prior context_state", exc_info=True)

    source_messages = _to_agent_messages(messages)
    if not source_messages:
        current_context = _build_current_context_state(source_messages)
        merged_state = _merge_prior_and_current_context_state(
            prior_context_state=prior_context_state,
            current_context_state=current_context,
            compaction_level="none",
            source_message_count=0,
            recent_count=recent_count,
        )
        return [], merged_state

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

    if current_tokens > soft_threshold:
        micro_messages = _micro_compact_messages(source_messages, recent_count=recent_count)
        micro_tokens = estimate_messages_tokens(micro_messages)
        prepared_messages = micro_messages
        compaction_level = "micro"

        if micro_tokens > hard_threshold:
            full_messages, _stats = await compact_messages(
                messages=micro_messages,
                max_tokens=hard_threshold,
                context_window=context_window,
                recent_count=recent_count,
            )
            compaction_level = "full"
            recent_window = _select_recent_window_for_full(full_messages, recent_count)
            current_context_for_full = _build_current_context_state(full_messages)
            merged_for_full = _merge_prior_and_current_context_state(
                prior_context_state=prior_context_state,
                current_context_state=current_context_for_full,
                compaction_level=compaction_level,
                source_message_count=len(source_messages),
                recent_count=recent_count,
            )
            synthetic_summary_message = _build_synthetic_summary_message(merged_for_full)
            prepared_messages = fix_tool_call_consistency([synthetic_summary_message] + recent_window)

    current_context = _build_current_context_state(prepared_messages)
    merged_state = _merge_prior_and_current_context_state(
        prior_context_state=prior_context_state,
        current_context_state=current_context,
        compaction_level=compaction_level,
        source_message_count=len(source_messages),
        recent_count=recent_count,
    )

    if stage == "post_turn":
        # Post-turn state should reflect latest run completion, regardless of compaction path.
        latest_context = _build_current_context_state(source_messages)
        merged_state = _merge_prior_and_current_context_state(
            prior_context_state=merged_state,
            current_context_state=latest_context,
            compaction_level=compaction_level,
            source_message_count=len(source_messages),
            recent_count=recent_count,
        )

    if compaction_level == "none":
        return list(messages), merged_state
    return _to_dict_messages(prepared_messages), merged_state


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
