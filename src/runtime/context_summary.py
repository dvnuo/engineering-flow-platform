"""Deterministic context-state extraction and summary helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from src.utils.truncate import truncate

_PREVIEW_LIMIT = 240
_SUMMARY_LIMIT = 320
_NEXT_STEP_FALLBACK = "Continue from the latest state and preserve prior constraints."
_CONSTRAINT_KEYWORDS = ("must", "should", "need to", "do not", "don't", "不要", "不能", "必须")
_DECISION_HINTS = ("decide", "decided", "decision", "we will", "chosen", "选择", "决定", "采用")
_OPEN_LOOP_HINTS = ("todo", "follow up", "pending", "next", "blocker", "wait", "待办", "后续", "未完成", "需要")


def _truncate(value: str, limit: int) -> str:
    return truncate(value, limit) if value else ""


def _msg_attr(message: Any, key: str, default: Any = None) -> Any:
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _iter_sentences(text: str) -> Iterable[str]:
    for sentence in re.split(r"[\n\.。!！?？]", text or ""):
        candidate = sentence.strip()
        if candidate:
            yield candidate


def safe_text(value: Any) -> str:
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


def extract_objective(messages: Sequence[Any]) -> str:
    first_user = ""
    latest_user = ""
    for msg in messages:
        if _msg_attr(msg, "role", "") != "user":
            continue
        text = safe_text(_msg_attr(msg, "content", ""))
        if not text:
            continue
        if not first_user:
            first_user = text
        latest_user = text
    return _truncate(first_user or latest_user, _PREVIEW_LIMIT)


def extract_constraints(messages: Sequence[Any]) -> list[str]:
    constraints: List[str] = []
    for msg in reversed(list(messages)):
        if _msg_attr(msg, "role", "") != "user":
            continue
        text = safe_text(_msg_attr(msg, "content", ""))
        if not text:
            continue
        for sentence in _iter_sentences(text):
            lower = sentence.lower()
            if any(keyword in lower or keyword in sentence for keyword in _CONSTRAINT_KEYWORDS):
                normalized = _truncate(sentence, 140)
                if normalized and normalized not in constraints:
                    constraints.append(normalized)
                    if len(constraints) >= 5:
                        return constraints
    return constraints


def extract_current_state(messages: Sequence[Any]) -> str:
    for msg in reversed(list(messages)):
        if _msg_attr(msg, "role", "") == "assistant":
            text = safe_text(_msg_attr(msg, "content", ""))
            if text:
                return _truncate(text, 220)
    for msg in reversed(list(messages)):
        if _msg_attr(msg, "role", "") == "tool":
            text = safe_text(_msg_attr(msg, "content", ""))
            if text:
                return _truncate(f"Latest tool result: {text}", 220)
    return ""


def extract_next_step(messages: Sequence[Any]) -> str:
    for msg in reversed(list(messages)):
        if _msg_attr(msg, "role", "") != "assistant":
            continue
        text = safe_text(_msg_attr(msg, "content", ""))
        if not text:
            continue
        lowered = text.lower()
        if any(token in lowered for token in ("next", "continue", "please", "run", "check", "verify", "then", "should")):
            return _truncate(text, 180)
    return _NEXT_STEP_FALLBACK


def extract_decisions(messages: Sequence[Any]) -> list[str]:
    decisions: List[str] = []
    for msg in reversed(list(messages)):
        if _msg_attr(msg, "role", "") not in {"assistant", "user"}:
            continue
        text = safe_text(_msg_attr(msg, "content", ""))
        if not text:
            continue
        for sentence in _iter_sentences(text):
            lower = sentence.lower()
            if any(hint in lower or hint in sentence for hint in _DECISION_HINTS):
                normalized = _truncate(sentence, 140)
                if normalized and normalized not in decisions:
                    decisions.append(normalized)
                    if len(decisions) >= 5:
                        return decisions
    return decisions


def extract_open_loops(messages: Sequence[Any]) -> list[str]:
    open_loops: List[str] = []
    for msg in reversed(list(messages)):
        if _msg_attr(msg, "role", "") not in {"assistant", "user"}:
            continue
        text = safe_text(_msg_attr(msg, "content", ""))
        if not text:
            continue
        for sentence in _iter_sentences(text):
            lower = sentence.lower()
            if "?" in sentence or any(hint in lower or hint in sentence for hint in _OPEN_LOOP_HINTS):
                normalized = _truncate(sentence, 140)
                if normalized and normalized not in open_loops:
                    open_loops.append(normalized)
                    if len(open_loops) >= 5:
                        return open_loops
    return open_loops


def _merge_unique(prior_values: Any, current_values: Any, *, limit: int = 5) -> list[str]:
    merged: List[str] = []
    for raw in list(prior_values or []) + list(current_values or []):
        text = _truncate(str(raw or "").strip(), 160)
        if text and text not in merged:
            merged.append(text)
            if len(merged) >= limit:
                break
    return merged


def _is_weaker_objective(current: str, prior: str) -> bool:
    if not prior:
        return False
    if not current:
        return True
    current_clean = current.strip()
    prior_clean = prior.strip()
    if len(current_clean) < 12 <= len(prior_clean):
        return True
    if len(current_clean) * 2 < len(prior_clean):
        return True
    return False


def merge_context_state(
    prior_context_state: dict | None,
    current_context_state: dict | None,
    *,
    compaction_level: str,
    source_message_count: int,
    recent_count: int,
) -> dict:
    prior = dict(prior_context_state or {})
    current = dict(current_context_state or {})

    prior_objective = _truncate(str(prior.get("objective") or "").strip(), _PREVIEW_LIMIT)
    current_objective = _truncate(str(current.get("objective") or "").strip(), _PREVIEW_LIMIT)
    objective = prior_objective if _is_weaker_objective(current_objective, prior_objective) else current_objective

    next_step = _truncate(str(current.get("next_step") or "").strip(), 180)
    if not next_step or next_step == _NEXT_STEP_FALLBACK:
        next_step = _truncate(str(prior.get("next_step") or "").strip(), 180) or _NEXT_STEP_FALLBACK

    merged = {
        "version": "context.v1",
        "compaction_level": compaction_level,
        "objective": objective,
        "current_state": _truncate(str(current.get("current_state") or prior.get("current_state") or "").strip(), 220),
        "next_step": next_step,
        "constraints": _merge_unique(prior.get("constraints"), current.get("constraints"), limit=5),
        "decisions": _merge_unique(prior.get("decisions"), current.get("decisions"), limit=5),
        "open_loops": _merge_unique(prior.get("open_loops"), current.get("open_loops"), limit=5),
        "source_message_count": source_message_count,
        "recent_window_count": recent_count,
        "last_compacted_at": datetime.now(timezone.utc).isoformat() if compaction_level in {"micro", "full"} else None,
    }
    merged["summary"] = build_structured_summary(merged)
    merged["recovery_context_message"] = build_recovery_context_message(merged)
    return merged


def build_structured_summary(context_state: dict) -> str:
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


def build_recovery_context_message(context_state: dict) -> str:
    lines = ["Recovered context:"]
    objective = _truncate(str(context_state.get("objective") or "").strip(), 200)
    current_state = _truncate(str(context_state.get("current_state") or "").strip(), 220)
    constraints = "; ".join(_merge_unique([], context_state.get("constraints"), limit=5))
    open_loops = "; ".join(_merge_unique([], context_state.get("open_loops"), limit=5))
    next_step = _truncate(str(context_state.get("next_step") or "").strip(), 180)

    if objective:
        lines.append(f"- Objective: {objective}")
    if current_state:
        lines.append(f"- Current state: {current_state}")
    if constraints:
        lines.append(f"- Constraints: {constraints}")
    if open_loops:
        lines.append(f"- Open loops: {open_loops}")
    if next_step:
        lines.append(f"- Next step: {next_step}")

    lines.append("Continue from this state without discarding prior constraints.")
    return _truncate("\n".join(lines), 420)


def build_context_state_from_messages(
    messages: Sequence[Any],
    *,
    prior_context_state: dict | None = None,
    compaction_level: str = "none",
    source_message_count: int = 0,
    recent_count: int = 5,
) -> dict:
    current = {
        "objective": extract_objective(messages),
        "current_state": extract_current_state(messages),
        "next_step": extract_next_step(messages),
        "constraints": extract_constraints(messages),
        "decisions": extract_decisions(messages),
        "open_loops": extract_open_loops(messages),
    }
    return merge_context_state(
        prior_context_state,
        current,
        compaction_level=compaction_level,
        source_message_count=source_message_count,
        recent_count=recent_count,
    )
