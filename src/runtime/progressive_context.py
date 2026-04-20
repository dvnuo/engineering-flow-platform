"""Progressive context preparation and durable context state helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Sequence, Set, Tuple

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
from src.context_blob_store import build_section_map, put_text

logger = logging.getLogger(__name__)


def _to_agent_messages(messages: List[Dict[str, Any]]) -> List[AgentMessage]:
    return [
        AgentMessage(
            role=str(msg.get("role") or "user"),
            content=msg.get("content", ""),
            timestamp=msg.get("timestamp"),
            tool_calls=msg.get("tool_calls"),
            tool_use_id=msg.get("tool_call_id") or msg.get("tool_use_id"),
            tool_name=msg.get("tool_name"),
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
        if getattr(msg, "tool_name", None):
            item["tool_name"] = msg.tool_name
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
                        tool_name=getattr(msg, "tool_name", None),
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
                        tool_name=getattr(msg, "tool_name", None),
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


def resolve_prompt_budget(*, stage: str, model: str | None) -> Dict[str, int]:
    llm_cfg = config.llm if isinstance(config.llm, dict) else {}
    budget_cfg = llm_cfg.get("context_budget") if isinstance(llm_cfg.get("context_budget"), dict) else {}
    default_cfg = budget_cfg.get("default") if isinstance(budget_cfg.get("default"), dict) else {}
    stage_cfg = budget_cfg.get(stage) if isinstance(budget_cfg.get(stage), dict) else {}
    merged = {**default_cfg, **stage_cfg}
    context_window = int(resolve_context_window_tokens(model))
    configured_reserved = int(merged.get("reserved_output_tokens", 8000) or 8000)
    configured_safety = int(merged.get("safety_margin_tokens", 4000) or 4000)
    max_prompt_tokens = int(merged.get("max_prompt_tokens", 50000) or 50000)
    effective_reserved = min(configured_reserved, int(context_window * 0.25))
    effective_safety = min(configured_safety, int(context_window * 0.05))
    context_based_prompt_cap = context_window - effective_reserved - effective_safety
    base_prompt = min(max_prompt_tokens, context_based_prompt_cap)
    if context_window < 4000:
        prompt_budget_tokens = max(1, base_prompt)
    else:
        prompt_budget_tokens = max(4000, base_prompt)
    return {
        "context_window_tokens": context_window,
        "prompt_budget_tokens": int(prompt_budget_tokens),
        "reserved_output_tokens": int(effective_reserved),
        "safety_margin_tokens": int(effective_safety),
        "max_prompt_tokens": int(max_prompt_tokens),
    }


def _extract_deterministic_assistant_summary(text: str, *, summary_max_chars: int = 2000) -> str:
    lines = str(text or "").splitlines()
    picks: List[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if re.match(r"^#{1,6}\s+", s):
            picks.append(s)
        elif s.lower().startswith(("feature:", "scenario:", "scenario outline:")):
            picks.append(s)
        elif re.search(r"\b(class|def|function|method)\s+[A-Za-z_][A-Za-z0-9_]*", s):
            picks.append(s)
        elif re.search(r"(error|exception|failed|traceback)", s, re.IGNORECASE):
            picks.append(s)
        elif re.search(r"[A-Za-z0-9_/.-]+\.[a-zA-Z0-9]{1,8}", s):
            picks.append(s)
        if len("\n".join(picks)) >= summary_max_chars:
            break
    if not picks:
        head = text[: min(600, len(text))]
        tail = text[-min(600, len(text)) :] if len(text) > 600 else ""
        picks = [head] + ([f"...\n{tail}"] if tail else [])
    summary = "\n".join(picks)
    return truncate(summary, summary_max_chars)


def _project_large_history_messages(
    messages: List[AgentMessage],
    *,
    session_id: str,
    recent_count: int,
    stage: str,
) -> Tuple[List[AgentMessage], Dict[str, Any]]:
    if stage not in {"pre_request", "tool_loop", "skill_generation", "tool_loop_aggressive", "post_turn"}:
        return list(messages), {"changed": False}
    projection_cfg = (config.llm.get("context_projection") if isinstance(config.llm, dict) else {}) or {}
    old_assistant_cfg = projection_cfg.get("old_assistant") if isinstance(projection_cfg.get("old_assistant"), dict) else {}
    assistant_limit = int(old_assistant_cfg.get("max_chars", 6000) or 6000)
    summary_max = int(old_assistant_cfg.get("summary_max_chars", 2000) or 2000)
    tool_cfg = projection_cfg.get("old_tool") if isinstance(projection_cfg.get("old_tool"), dict) else {}
    tool_limit = int(tool_cfg.get("max_chars", 8000) or 8000)
    preview_chars = int(tool_cfg.get("preview_chars", 2000) or 2000)
    keep_start = max(0, len(messages) - max(1, recent_count))
    protected_tool_chain_ids = _collect_protected_tool_chain_ids(messages, recent_count)
    refs: List[str] = []
    saved = 0
    projected_assistant = 0
    projected_tool = 0
    projected: List[AgentMessage] = []
    for idx, msg in enumerate(messages):
        if idx >= keep_start:
            projected.append(msg)
            continue
        text = str(msg.content or "")
        if msg.role == "assistant" and not msg.tool_calls and len(text) > assistant_limit:
            ref = put_text(session_id=session_id, kind="assistant_output", source_id=f"assistant_{idx}", title="assistant_output", content=text, metadata={"stage": stage})
            refs.append(ref)
            summary = _extract_deterministic_assistant_summary(text, summary_max_chars=summary_max)
            compact = (
                f"[old assistant output compacted | original_chars={len(text)} | ref={ref}]\n"
                f"Summary:\n{summary}\n\nFull original assistant output is available in the session transcript/context blob."
            )
            saved += max(0, len(text) - len(compact))
            projected_assistant += 1
            projected.append(AgentMessage(role="assistant", content=compact, timestamp=msg.timestamp, tool_calls=msg.tool_calls, tool_use_id=msg.tool_use_id, tool_name=getattr(msg, "tool_name", None)))
            continue
        if msg.role == "tool" and msg.tool_use_id and msg.tool_use_id in protected_tool_chain_ids:
            projected.append(msg)
            continue
        if msg.role == "tool" and len(text) > tool_limit:
            ref = put_text(session_id=session_id, kind="tool_output", source_id=msg.tool_use_id or f"tool_{idx}", title=msg.tool_name or "tool_output", content=text, metadata={"tool_name": msg.tool_name, "stage": stage})
            refs.append(ref)
            toc = build_section_map(text)
            toc_text = "\n".join(f"- {t.get('heading')} [{t.get('start')}..{t.get('end')}]" for t in toc[:10]) or "(no headings)"
            compact = (
                f"[old tool output compacted | original_chars={len(text)} | ref={ref}]\n"
                f"Section map:\n{toc_text}\nPreview:\n{truncate(text, preview_chars)}\n"
                f"Use context_read_ref(ref=\"{ref}\", section=\"raw\", max_chars=6000) for full text."
            )
            saved += max(0, len(text) - len(compact))
            projected_tool += 1
            projected.append(AgentMessage(role="tool", content=compact, timestamp=msg.timestamp, tool_use_id=msg.tool_use_id, tool_name=getattr(msg, "tool_name", None)))
            continue
        projected.append(msg)
    projected = fix_tool_call_consistency(projected)
    return projected, {
        "changed": projected != messages,
        "projected_old_assistant_messages": projected_assistant,
        "projected_old_tool_messages": projected_tool,
        "projection_chars_saved": saved,
        "context_blob_refs_created": refs,
    }


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
        tool_name=getattr(summary_message, "tool_name", None),
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


def _percent(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def _resolve_next_compaction_action(*, compaction_level: str, current_tokens: int, soft_threshold: int) -> str:
    if compaction_level == "full":
        return "full_compaction_applied"
    if compaction_level == "micro":
        return "micro_compaction_applied"
    if current_tokens >= int(soft_threshold * 0.9):
        return "approaching_micro_compaction"
    return "none"


def _resolve_next_pruning_policy(
    *,
    compaction_level: str,
    next_compaction_action: str,
) -> str:
    if compaction_level == "full":
        return "Full compaction applied: keep a synthetic recovery summary plus the recent message window; older history is represented by the summary."
    if compaction_level == "micro":
        return "Micro-compaction applied: summarize older turns and keep the recent message window/tool chain for continuity."
    if next_compaction_action == "approaching_micro_compaction":
        return "Approaching micro-compaction: if the request grows past the soft threshold, older turns will be summarized while recent turns and protected tool context are kept."
    return "No compaction planned yet: keep the prepared conversation as-is."


def _build_context_budget(
    *,
    stage: str,
    model: str | None,
    estimated_tokens: int,
    prepared_tokens: int,
    context_window: int,
    configured_threshold: float,
    soft_threshold: int,
    hard_threshold: int,
    compaction_level: str,
    recent_count: int,
    source_message_count: int,
    prepared_message_count: int,
    prompt_budget: Dict[str, int] | None = None,
    projection_stats: Dict[str, Any] | None = None,
    request_estimated_tokens: int | None = None,
) -> dict:
    next_action = _resolve_next_compaction_action(
        compaction_level=compaction_level,
        current_tokens=estimated_tokens,
        soft_threshold=soft_threshold,
    )
    budget = {
        "stage": stage,
        "model": model,
        "estimated_tokens": estimated_tokens,
        "prepared_tokens": prepared_tokens,
        "context_window_tokens": context_window,
        "usage_percent": _percent(estimated_tokens, context_window),
        "prepared_usage_percent": _percent(prepared_tokens, context_window),
        "configured_threshold_percent": round(configured_threshold * 100, 1),
        "soft_threshold_tokens": soft_threshold,
        "hard_threshold_tokens": hard_threshold,
        "soft_threshold_percent": _percent(soft_threshold, context_window),
        "hard_threshold_percent": _percent(hard_threshold, context_window),
        "tokens_until_soft_threshold": max(0, soft_threshold - estimated_tokens),
        "tokens_until_hard_threshold": max(0, hard_threshold - estimated_tokens),
        "compaction_level": compaction_level,
        "next_compaction_action": next_action,
        "next_pruning_policy": _resolve_next_pruning_policy(
            compaction_level=compaction_level,
            next_compaction_action=next_action,
        ),
        "recent_count": recent_count,
        "source_message_count": source_message_count,
        "prepared_message_count": prepared_message_count,
        "token_estimate": True,
    }
    if isinstance(prompt_budget, dict):
        budget.update(
            {
                "prompt_budget_tokens": prompt_budget.get("prompt_budget_tokens"),
                "reserved_output_tokens": prompt_budget.get("reserved_output_tokens"),
                "safety_margin_tokens": prompt_budget.get("safety_margin_tokens"),
                "max_prompt_tokens": prompt_budget.get("max_prompt_tokens"),
            }
        )
    if request_estimated_tokens is not None:
        budget["request_estimated_tokens"] = request_estimated_tokens
    if isinstance(projection_stats, dict):
        budget.update(
            {
                "projected_old_assistant_messages": projection_stats.get("projected_old_assistant_messages", 0),
                "projected_old_tool_messages": projection_stats.get("projected_old_tool_messages", 0),
                "projection_chars_saved": projection_stats.get("projection_chars_saved", 0),
                "context_blob_refs_created": projection_stats.get("context_blob_refs_created", []),
            }
        )
    return budget


def build_portal_context_preview(context_state: dict | None) -> dict:
    if not isinstance(context_state, dict):
        return {}
    preview = {
        "context_compaction_level": context_state.get("compaction_level"),
        "context_objective_preview": truncate(str(context_state.get("objective") or ""), 140),
        "context_summary_preview": truncate(str(context_state.get("summary") or ""), 180),
        "context_next_step_preview": truncate(str(context_state.get("next_step") or ""), 140),
    }
    budget = context_state.get("budget") if isinstance(context_state.get("budget"), dict) else {}
    if budget:
        usage_percent = budget.get("prepared_usage_percent")
        if usage_percent in (None, ""):
            usage_percent = budget.get("usage_percent")
        estimated_tokens = budget.get("prepared_tokens")
        if estimated_tokens in (None, ""):
            estimated_tokens = budget.get("estimated_tokens")
        preview.update(
            {
                "context_usage_percent": usage_percent,
                "context_estimated_tokens": estimated_tokens,
                "context_window_tokens": budget.get("context_window_tokens"),
                "context_next_compaction_action": budget.get("next_compaction_action"),
                "context_next_pruning_policy": budget.get("next_pruning_policy"),
                "context_tokens_until_soft_threshold": budget.get("tokens_until_soft_threshold"),
                "context_tokens_until_hard_threshold": budget.get("tokens_until_hard_threshold"),
            }
        )
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
    prompt_budget = resolve_prompt_budget(stage=stage, model=model)
    context_window = int(prompt_budget.get("context_window_tokens") or resolve_context_window_tokens(model))
    configured_threshold = normalize_compaction_threshold(config.llm.get("compaction_threshold", 0.8), 0.8)
    percentage_soft, percentage_hard = _resolve_stage_thresholds(
        stage=stage,
        context_window=context_window,
        hard_threshold=int(context_window * configured_threshold),
    )
    prompt_budget_tokens = int(prompt_budget.get("prompt_budget_tokens", percentage_hard))
    soft_threshold = min(percentage_soft, prompt_budget_tokens)
    hard_threshold = min(percentage_hard, prompt_budget_tokens)

    if not source_messages:
        state = build_context_state_from_messages(
            source_messages,
            prior_context_state=prior_context_state,
            compaction_level="none",
            source_message_count=0,
            recent_count=recent_count,
        )
        annotated_state = _annotate_context_state(state, summary_source="none", history_from_count=0, history_to_count=0)
        annotated_state["budget"] = _build_context_budget(
            stage=stage,
            model=model,
            estimated_tokens=0,
            prepared_tokens=0,
            context_window=context_window,
            configured_threshold=configured_threshold,
            soft_threshold=soft_threshold,
            hard_threshold=hard_threshold,
            compaction_level="none",
            recent_count=recent_count,
            source_message_count=0,
            prepared_message_count=0,
            prompt_budget=prompt_budget,
        )
        return [], annotated_state

    projected_messages, projection_stats = _project_large_history_messages(
        source_messages,
        session_id=session_id,
        recent_count=recent_count,
        stage=stage,
    )
    projection_changed = bool(projection_stats.get("changed"))
    current_tokens = estimate_messages_tokens(projected_messages)
    compaction_level = "none"
    prepared_messages = list(projected_messages)
    summary_source = "none"

    if current_tokens > soft_threshold:
        micro_messages = _micro_compact_messages(projected_messages, recent_count=recent_count)
        prepared_messages = micro_messages
        compaction_level = "micro" if not projection_changed else "projection_micro"
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
                projected_messages,
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
            projected_messages,
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

    if projection_changed and compaction_level == "none":
        compaction_level = "projection"
        summary_source = "projection"
    final_messages = _to_dict_messages(prepared_messages) if (compaction_level != "none" or projection_changed) else list(messages)
    prepared_tokens = estimate_messages_tokens(_to_agent_messages(final_messages))
    annotated_state = _annotate_context_state(
        merged_state,
        summary_source=summary_source,
        history_from_count=source_count,
        history_to_count=len(final_messages),
    )
    annotated_state["budget"] = _build_context_budget(
        stage=stage,
        model=model,
        estimated_tokens=current_tokens,
        prepared_tokens=prepared_tokens,
        context_window=context_window,
        configured_threshold=configured_threshold,
        soft_threshold=soft_threshold,
        hard_threshold=hard_threshold,
        compaction_level=compaction_level,
        recent_count=recent_count,
        source_message_count=source_count,
        prepared_message_count=len(final_messages),
        prompt_budget=prompt_budget,
        projection_stats=projection_stats,
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

    await session_manager.set_context_state(session_id, context_state)
    return context_state
