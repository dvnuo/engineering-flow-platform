"""Agent core implementation following modern agent loop patterns."""

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import platform
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.agents.skill_mode import (
    SkillSession,
    _build_skill_mode_system_prompt,
    _build_skill_mode_user_prompt,
    _extract_skill_artifacts,
    _merge_skill_artifacts,
    _parse_skill_control_marker,
    _update_skill_memory_summary,
    generate_initial_skill_plan,
)

from src.agents.heartbeat import get_heartbeat, start_heartbeat, stop_heartbeat
from src.agents.llm import (
    _normalize_provider_key,
    llm_client,
    is_vision_model,
    get_vision_fallback_model,
)
from src.agents.memory import memory_system
from src.memory.update_manager import MemoryUpdateManager
from src.agents.thinking import ThinkLevel, normalize_think_level, format_runtime_info
from src.config import config, resolve_output_boundary
from src.utils.truncate import truncate, truncate_with_count
from src.utils.redaction import safe_preview, redact_value, sanitize_exception_message
from src.sessions.manager import session_manager
from src.sessions.persistence import session_persistence
from src.agents.executor import (
    skills_executor,
    SkillResult,
    get_tools_schemas,
    execute_tool_by_name,  # compatibility export for tests and legacy patch points
    ToolResult,
)
from src.agents.skill_runtime import (
    build_skill_runtime_event_payload,
    build_skill_tool_denied_result,
    get_effective_skill_runtime_prompt,
    get_skill_reference_attachment,
    resolve_prompt_execution_boundary,
)
from src.skills.runtime import SkillRuntimeConfig
from src.skills.active_contract import (
    build_active_skill_contract,
    get_contract_skill_name,
    is_active_skill_contract_usable,
    is_clear_active_skill_command,
    parse_explicit_skill_switch_name,
)
from src.runtime.chat_orchestration_adapter import execute_tool_or_task_orchestration
from src.runtime import build_default_execution_bus
from src.runtime.display_blocks import normalize_display_blocks
from src.runtime.tool_filtering import (
    INTERNAL_SUPPORT_TOOL_NAMES,
    extract_tool_name,
    filter_tool_schemas_for_llm,
    intersect_tool_schemas_by_names,
)
from src.runtime.progressive_context import (
    apply_progressive_context_after_turn,
    degrade_projected_context_sources,
    prepare_progressive_messages,
    resolve_prompt_budget,
)
from src.context_blob_store import build_section_map, put_text
from src.agents.compaction import estimate_tokens
from src.runtime.output_controller import call_llm_with_output_control, recover_max_output_tokens

logger = logging.getLogger(__name__)


def _run_pre_tool_hooks_via_governance(**kwargs):
    from src.runtime.governance_bus import run_pre_tool_hooks

    return run_pre_tool_hooks(**kwargs)


def _run_post_tool_hooks_via_governance(**kwargs):
    from src.runtime.governance_bus import run_post_tool_hooks

    return run_post_tool_hooks(**kwargs)



def _enrich_runtime_event_context(
    data: Dict[str, Any],
    *,
    session_id: str,
    agent_id: Optional[str] = None,
    request_id: Optional[str] = None,
    task_id: Optional[str] = None,
    group_id: Optional[str] = None,
    coordination_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fill runtime event context fields when absent, preserving explicit payload values."""
    merged: Dict[str, Any] = dict(data or {})
    context_fields = {
        "session_id": session_id,
        "agent_id": agent_id,
        "request_id": request_id,
        "task_id": task_id,
        "group_id": group_id,
        "coordination_run_id": coordination_run_id,
    }
    for key, value in context_fields.items():
        existing = merged.get(key)
        if existing is not None and (not isinstance(existing, str) or existing.strip()):
            continue
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        merged[key] = value
    return merged


def _build_runtime_event_record(event_type: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": event_type,
        "event_type": event_type,
        "state": event_data.get("state") or event_data.get("status") or "",
        "session_id": event_data.get("session_id"),
        "request_id": event_data.get("request_id"),
        "agent_id": event_data.get("agent_id"),
        "summary": event_data.get("message") or event_data.get("summary") or event_type,
        "data": dict(event_data),
        "detail_payload": dict(event_data),
        "ts": datetime.utcnow().isoformat() + "Z",
    }


def _is_meaningful_context_state(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False

    text_fields = (
        "objective",
        "summary",
        "current_state",
        "next_step",
        "recovery_context_message",
        "compaction_level",
    )
    for field in text_fields:
        field_value = value.get(field)
        if isinstance(field_value, str) and field_value.strip():
            return True

    list_fields = ("constraints", "decisions", "open_loops")
    for field in list_fields:
        field_value = value.get(field)
        if isinstance(field_value, list) and field_value:
            for item in field_value:
                if str(item).strip():
                    return True

    budget = value.get("budget")
    if isinstance(budget, dict):
        for budget_value in budget.values():
            if budget_value not in (None, "", [], {}):
                return True

    return False


def _build_terminal_context_snapshot_event(
    *,
    context_state: Dict[str, Any],
    session_id: str,
    agent_id: Optional[str],
    request_id: Optional[str],
    status: str,
) -> Optional[Dict[str, Any]]:
    if not _is_meaningful_context_state(context_state):
        return None

    budget = context_state.get("budget") if isinstance(context_state.get("budget"), dict) else {}
    event_data = {
        "stage": "post_turn",
        "terminal": True,
        "state": status,
        "message": "Final context snapshot",
        "summary": "Final context snapshot",
        "context_state": context_state,
        "budget": budget,
    }
    enriched_event_data = _enrich_runtime_event_context(
        event_data,
        session_id=session_id,
        agent_id=agent_id,
        request_id=request_id,
    )
    return _build_runtime_event_record("context_snapshot", enriched_event_data)


def _has_terminal_post_turn_context_snapshot(events: Any) -> bool:
    if not isinstance(events, list):
        return False
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type") or event.get("event_type")
        if event_type != "context_snapshot":
            continue
        data_payload = event.get("data") if isinstance(event.get("data"), dict) else {}
        detail_payload = event.get("detail_payload") if isinstance(event.get("detail_payload"), dict) else {}
        stage = data_payload.get("stage")
        if stage is None:
            stage = detail_payload.get("stage")
        terminal = data_payload.get("terminal")
        if terminal is None:
            terminal = detail_payload.get("terminal")
        context_state = data_payload.get("context_state")
        if not _is_meaningful_context_state(context_state):
            context_state = detail_payload.get("context_state")
        if stage == "post_turn" and terminal is True and _is_meaningful_context_state(context_state):
            return True
    return False


def _resolve_effective_max_tokens(model_max_tokens: int) -> Tuple[int, Dict[str, Any]]:
    llm_cfg = config.llm if isinstance(config.llm, dict) else {}
    allow_lower = bool(llm_cfg.get("allow_lower_max_tokens_than_model_limit", False))
    configured_raw = llm_cfg.get("max_tokens")
    configured_max_tokens: Optional[int]
    try:
        configured_max_tokens = int(configured_raw) if configured_raw is not None else None
    except Exception:
        configured_max_tokens = None
    if configured_max_tokens is not None and configured_max_tokens <= 0:
        configured_max_tokens = None
    legacy_ignored = False
    if configured_max_tokens is not None and configured_max_tokens < int(model_max_tokens) and not allow_lower:
        effective = int(model_max_tokens)
        legacy_ignored = True
    else:
        effective = min(int(model_max_tokens), int(configured_max_tokens or model_max_tokens))
    return effective, {
        "configured_max_tokens": configured_max_tokens,
        "effective_max_tokens": effective,
        "legacy_max_tokens_ignored": legacy_ignored,
    }


def _merge_request_budget_into_context_state(
    context_state: Dict[str, Any],
    latest_request_budget: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged = dict(context_state or {})
    if not isinstance(latest_request_budget, dict) or not latest_request_budget:
        return merged
    budget = merged.get("budget") if isinstance(merged.get("budget"), dict) else {}
    merged_budget = dict(budget)
    for key in (
        "request_estimated_tokens",
        "prompt_budget_tokens",
        "reserved_output_tokens",
        "safety_margin_tokens",
        "max_prompt_tokens",
        "max_output_tokens",
        "request_over_budget",
        "projected_old_assistant_messages",
        "projected_recent_assistant_messages",
        "projected_plain_assistant_messages",
        "projected_old_tool_messages",
        "projection_chars_saved",
        "assistant_projection_chars_saved",
        "context_blob_refs_created",
        "output_size_guard_applied",
        "large_generation_guard_applied",
        "request_budget_stage",
    ):
        if key in latest_request_budget:
            merged_budget[key] = latest_request_budget.get(key)
    if "request_budget_stage" not in merged_budget:
        stage = latest_request_budget.get("stage")
        if isinstance(stage, str) and stage.strip():
            merged_budget["request_budget_stage"] = stage.strip()
    merged["budget"] = merged_budget
    return merged

# Context variable for skill workdir - async-safe
_skill_workdir: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('skill_workdir', default=None)


def set_skill_workdir(path: Optional[str]) -> None:
    """Set the current skill working directory (async-safe)."""
    _skill_workdir.set(path)
    if path:
        logger.debug(f"[Skill] Workdir: {path}")


def get_skill_workdir() -> Optional[str]:
    """Get the current skill working directory (async-safe)."""
    return _skill_workdir.get()


# Debug logging is enabled when logger.level is DEBUG
# Set log_level: DEBUG in config.yaml to enable
# When DEBUG, complete input/output is logged (no truncation)

_DEBUG_ENABLED = None  # Lazy initialization
_TOOL_RESULT_GOVERNANCE_ATTR = "_governance"
DEFAULT_TOOL_FEEDBACK_MAX_LENGTH = 8000
LARGE_SOURCE_TOOL_PREFIXES = ("jira_", "confluence_")


def _tool_feedback_text(
    value: Any,
    max_length: Optional[int] = DEFAULT_TOOL_FEEDBACK_MAX_LENGTH,
) -> str:
    """Build bounded tool feedback text for next-round LLM input only.

    max_length <= 0 or None means no core-level truncation.
    """
    text = str(value)
    if not text:
        return "(empty)"
    if max_length is None or max_length <= 0:
        return text
    return truncate_with_count(text, max_length)


def _resolve_context_projection_cfg() -> Dict[str, Any]:
    llm_cfg = config.llm if isinstance(config.llm, dict) else {}
    projection = llm_cfg.get("context_projection") if isinstance(llm_cfg.get("context_projection"), dict) else {}
    return projection


def _build_large_source_envelope(
    *,
    tool_name: str,
    text: str,
    max_chars: int,
    session_id: Optional[str],
    source_id: Optional[str],
) -> str:
    def _extract_markdown_section(raw: str, patterns: tuple[str, ...], max_chars: int) -> str:
        lines = raw.splitlines()
        starts = []
        for i, line in enumerate(lines):
            if line.strip().startswith("#"):
                lowered = line.strip().lower()
                if any(p in lowered for p in patterns):
                    starts.append(i)
        if not starts:
            return ""
        s = starts[0]
        e = len(lines)
        for i in range(s + 1, len(lines)):
            if lines[i].strip().startswith("#"):
                e = i
                break
        return truncate_with_count("\n".join(lines[s:e]).strip(), max_chars)

    def _build_jira_model_view(raw: str, budget: int) -> str:
        title = ""
        status = ""
        issue_key = ""
        for line in raw.splitlines():
            stripped = line.strip()
            if not title and stripped.startswith("# "):
                title = stripped[2:].strip()
                key_match = re.search(r"\b([A-Z][A-Z0-9_]+-\d+)\b", title)
                if key_match:
                    issue_key = key_match.group(1)
            if not status and stripped.lower().startswith("**status:**"):
                status = stripped.split(":", 1)[1].strip() if ":" in stripped else stripped
            if not issue_key:
                m = re.search(r"\b([A-Z][A-Z0-9_]+-\d+)\b", stripped)
                if m:
                    issue_key = m.group(1)
            if title and status and issue_key:
                break
        chunks = []
        metadata_lines = ["Metadata:"]
        if title:
            metadata_lines.append(f"- title: {title}")
        if status:
            metadata_lines.append(f"- status: {status}")
        if issue_key:
            metadata_lines.append(f"- issue_key: {issue_key}")
        if len(metadata_lines) > 1:
            chunks.append("\n".join(metadata_lines))
        for patterns in (
            ("acceptance criteria", "requirements", "business rules", " ac"),
            ("description",),
            ("comments", "recent comments"),
            ("attachments",),
        ):
            section = _extract_markdown_section(raw, patterns, max(400, budget // 3))
            if section:
                chunks.append(section)
        if not chunks:
            chunks.append(truncate_with_count(raw, budget))
        return truncate_with_count("\n\n".join(chunks), budget)

    def _build_confluence_model_view(raw: str, budget: int) -> str:
        toc = build_section_map(raw)
        toc_text = "\n".join(f"- {item.get('heading')}" for item in toc[:12])
        opening = truncate_with_count(raw, max(600, budget // 2))
        return truncate_with_count(f"TOC:\n{toc_text or '(no headings)'}\n\nOpening preview:\n{opening}", budget)

    kind = "jira_issue" if tool_name.startswith("jira_") else "confluence_page" if tool_name.startswith("confluence_") else "large_source"
    session_key = session_id or "unknown_session"
    ref = put_text(
        session_id=session_key,
        kind=kind,
        source_id=source_id or tool_name,
        title=f"{tool_name} result",
        content=text,
        metadata={"tool_name": tool_name},
    )
    section_map = build_section_map(text)
    if kind == "jira_issue":
        preview = _build_jira_model_view(text, max_chars)
    elif kind == "confluence_page":
        preview = _build_confluence_model_view(text, max_chars)
    else:
        preview = truncate_with_count(text, max_chars)
    toc = "\n".join(f"- {item.get('heading')} (chars {item.get('start')}..{item.get('end')})" for item in section_map[:16]) or "(no headings found)"
    model_view_chars = len(preview)
    return (
        f"[large source tool result projected]\n"
        f"tool_name: {tool_name}\n"
        f"kind: {kind}\n"
        f"context_ref: {ref}\n"
        f"original_chars: {len(text)}\n"
        f"model_view_chars: {model_view_chars}\n"
        f"full_content_available: true\n"
        f"section_map:\n{toc}\n\n"
        f"preview:\n{preview}\n\n"
        f"To read more, call: context_read_ref(ref=\"{ref}\", section=\"raw\", max_chars=6000)\n"
        f"To read headings only: context_read_ref(ref=\"{ref}\", section=\"toc\", max_chars=4000)"
    )


def _is_projected_large_source_feedback(text: str) -> bool:
    value = str(text or "").lstrip()
    return value.startswith("[large source tool result projected]") and "context_ref: ctx://context/" in value


def _is_projected_feedback(text: str) -> bool:
    value = str(text or "").lstrip()
    if value.startswith("[large source tool result projected]"):
        return "context_ref: ctx://context/" in value
    if (
        value.startswith("[assistant output compacted")
        or value.startswith("[old tool output compacted")
        or value.startswith("[old assistant output compacted")
        or value.startswith("[assistant tool-call content compacted")
        or value.startswith("[assistant skill content compacted")
    ):
        return "ref=ctx://context/" in value or "ctx://context/" in value
    return False


def _tool_feedback_text_for_tool(
    tool_name: Optional[str],
    value: Any,
    *,
    session_id: Optional[str] = None,
    source_id: Optional[str] = None,
) -> str:
    """Build tool feedback for the next LLM call using per-tool truncation policy."""
    text = str(value)
    if _is_projected_feedback(text):
        return text
    if not (tool_name and tool_name.startswith(LARGE_SOURCE_TOOL_PREFIXES)):
        return _tool_feedback_text(text, max_length=DEFAULT_TOOL_FEEDBACK_MAX_LENGTH)
    if _is_projected_large_source_feedback(text):
        return text
    projection_cfg = _resolve_context_projection_cfg()
    jira_conf_cfg = projection_cfg.get("jira_confluence") if isinstance(projection_cfg.get("jira_confluence"), dict) else {}
    max_chars = int(jira_conf_cfg.get("model_feedback_max_chars", DEFAULT_TOOL_FEEDBACK_MAX_LENGTH) or DEFAULT_TOOL_FEEDBACK_MAX_LENGTH)
    if len(text) <= max_chars:
        return text
    return _build_large_source_envelope(
        tool_name=tool_name,
        text=text,
        max_chars=max_chars,
        session_id=session_id,
        source_id=source_id,
    )


def estimate_llm_request_tokens(input_items: List[Dict[str, Any]], system_prompt: str, tools: List[Dict[str, Any]]) -> int:
    raw = json.dumps({"input_items": input_items, "system_prompt": system_prompt or "", "tools": tools or []}, ensure_ascii=False, default=str)
    return estimate_tokens(raw)


def _should_abort_over_budget(request_estimated_tokens: int, prompt_budget_tokens: int, *, tolerance_ratio: float = 0.05) -> bool:
    if prompt_budget_tokens <= 0:
        return False
    allowed = int(prompt_budget_tokens * (1.0 + max(0.0, tolerance_ratio)))
    return request_estimated_tokens > allowed


def _build_context_budget_exceeded_error(
    *,
    request_estimated_tokens: int,
    loop_budget: Dict[str, Any],
    stage: str = "tool_loop",
) -> Dict[str, Any]:
    return {
        "error": "LLM request remains over prompt budget after context projection.",
        "error_type": "context_budget_exceeded",
        "code": "context_budget_exceeded",
        "details": {
            "request_estimated_tokens": request_estimated_tokens,
            "prompt_budget_tokens": loop_budget.get("prompt_budget_tokens"),
            "reserved_output_tokens": loop_budget.get("reserved_output_tokens"),
            "safety_margin_tokens": loop_budget.get("safety_margin_tokens"),
            "max_prompt_tokens": loop_budget.get("max_prompt_tokens"),
            "max_output_tokens": loop_budget.get("max_output_tokens"),
            "request_over_budget": True,
            "request_budget_stage": stage,
            "stage": stage,
            "suggestion": "Split generation into smaller steps or write artifacts/files instead of emitting full content in chat.",
        },
    }


def _safe_request_budget_fields(budget: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(budget, dict):
        return {}
    keys = (
        "request_estimated_tokens",
        "prompt_budget_tokens",
        "reserved_output_tokens",
        "safety_margin_tokens",
        "max_prompt_tokens",
        "max_output_tokens",
        "request_over_budget",
        "output_size_guard_applied",
        "large_generation_guard_applied",
        "large_generation_guard_reason",
        "generation_mode",
        "current_generation_phase",
        "output_risk_level",
        "max_chat_output_chars",
        "output_token_limit",
        "input_context_usage_percent",
        "max_output_recovery_applied",
        "max_output_recovery_attempts",
    )
    safe = {key: budget.get(key) for key in keys if key in budget}
    stage = budget.get("request_budget_stage")
    if not isinstance(stage, str) or not stage.strip():
        stage = budget.get("stage")
    if isinstance(stage, str) and stage.strip():
        safe["request_budget_stage"] = stage.strip()
    return safe


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _merge_budget_into_error_details(error_response: Dict[str, Any], budget: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(error_response, dict):
        return error_response
    safe_budget = _safe_request_budget_fields(budget)
    if not safe_budget:
        return error_response
    details = error_response.get("details") if isinstance(error_response.get("details"), dict) else {}
    merged = dict(details)
    merged.update(safe_budget)
    error_response["details"] = merged
    error_response["request_budget"] = safe_budget
    return error_response


def project_skill_assistant_content_for_input_items(
    raw_output: str,
    *,
    session_id: Optional[str],
    round_num: int,
    summary_max_chars: int = 2000,
) -> str:
    text = str(raw_output or "")
    if not text:
        return ""
    if _is_projected_feedback(text):
        return text
    summary_lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("```"):
            summary_lines.append(stripped)
        elif re.match(r"^(Feature|Scenario|Scenario Outline):", stripped):
            summary_lines.append(stripped)
        elif re.search(r"\b(class|def|function|Given|When|Then|And)\b", stripped):
            summary_lines.append(stripped)
        if len("\n".join(summary_lines)) >= summary_max_chars:
            break
    summary = truncate_with_count("\n".join(summary_lines).strip() or text, summary_max_chars)
    session_key = session_id or "unknown_session"
    ref = put_text(
        session_id=session_key,
        kind="assistant_skill_output",
        source_id=f"skill_round_{round_num}",
        title=f"Skill assistant output round {round_num}",
        content=text,
        metadata={"source": "skill_mode_assistant"},
    )
    return (
        f"[assistant skill content compacted | original_chars={len(text)} | ref={ref}]\n"
        f"Summary:\n{summary}\n\n"
        f"Full assistant text is available through context_read_ref(ref=\"{ref}\")."
    )


def _should_project_assistant_context(content_text: str) -> bool:
    text = str(content_text or "")
    if not text:
        return False
    if _is_projected_feedback(text):
        return False
    return (
        len(text) > DEFAULT_TOOL_FEEDBACK_MAX_LENGTH
        or "```" in text
        or re.search(r"\b(Feature:|Scenario:|Scenario Outline:|class\s+\w+|def\s+\w+)\b", text) is not None
    )


def _large_generation_output_guard(
    active_skill_runtime: Optional[Any],
    selected_skill: Optional[Any],
    active_skill_contract: Optional[Dict[str, Any]],
    latest_user_text: str = "",
) -> str:
    skill_name = (
        getattr(active_skill_runtime, "skill_name", None)
        or getattr(selected_skill, "name", None)
        or (active_skill_contract or {}).get("skill_name")
    )
    normalized = str(skill_name or "").strip().lower()
    skill_desc = str(getattr(selected_skill, "description", "") or "")
    skill_or_desc = f"{normalized} {skill_desc}".lower()
    user_text = str(latest_user_text or "").lower()
    generation_markers = (
        "test", "code", "generate", "implementation", "spec", "feature", "step definition", "step_definitions",
        "multi-file", "all test cases", "generate all", "全部内容", "生成全部", "all jira information",
    )
    is_generation = any(marker in skill_or_desc for marker in generation_markers) or any(
        marker in user_text for marker in generation_markers
    )
    if not is_generation:
        return ""
    boundary = resolve_output_boundary(
        getattr(active_skill_runtime, "model", None) or getattr(selected_skill, "model", None) or config.llm.get("model")
    )
    max_chars = int(boundary.get("max_chat_output_chars") or 0)
    max_tokens = int(boundary.get("max_chat_output_tokens") or 0)
    return (
        "Large generation output guard: Do not emit a complete multi-file implementation or very long generated "
        "artifact in a single chat response. Generate one bounded phase/file at a time. Prefer writing artifacts/files "
        "through tools when available. In chat, return a concise manifest, file paths, and next step. "
        f"Keep response within resolved output boundary (tokens≈{max_tokens}, chars≈{max_chars}).\n"
        f"generation_mode=staged\nmax_chat_output_chars={max_chars}\ncurrent_phase=manifest"
    )


def _extract_jira_key_or_url(text: str) -> str:
    raw = str(text or "")
    url_match = re.search(r"https?://[^\s]+/browse/([A-Z][A-Z0-9_]*-\d+)", raw, re.IGNORECASE)
    if url_match:
        return url_match.group(0)
    key_match = re.search(r"\b([A-Z][A-Z0-9_]*-\d+)\b", raw)
    return key_match.group(1) if key_match else ""


def _extract_confluence_page_hint(text: str) -> str:
    raw = str(text or "")
    url_match = re.search(r"https?://[^\s]+(?:/pages/\d+/?[^\s]*|[?&]pageId=\d+)", raw, re.IGNORECASE)
    if url_match:
        return url_match.group(0)
    return ""


def _find_source_target_for_context(
    latest_user_text: str,
    messages: List[Dict[str, Any]],
    context_state: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    source = context_state.get("source") if isinstance(context_state, dict) and isinstance(context_state.get("source"), dict) else {}
    candidates: List[str] = [str(latest_user_text or "")]
    if isinstance(source, dict):
        for key in ("target", "latest_target", "issue_key", "page_id", "last_source_hint"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    for msg in reversed(messages or []):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            candidates.append(content)
        if len(candidates) >= 12:
            break
    for text in candidates:
        jira = _extract_jira_key_or_url(text)
        if jira:
            return {"source_type": "jira", "target": jira, "existing_source_ref": str(source.get("source_ref") or ""), "existing_digest_ref": str(source.get("digest_ref") or "")}
        conf = _extract_confluence_page_hint(text)
        if conf:
            return {"source_type": "confluence", "target": conf, "existing_source_ref": str(source.get("source_ref") or ""), "existing_digest_ref": str(source.get("digest_ref") or "")}
    return {"source_type": "", "target": "", "existing_source_ref": str(source.get("source_ref") or ""), "existing_digest_ref": str(source.get("digest_ref") or "")}


def _requires_source_complete_context(
    user_text: str,
    active_skill_runtime: Optional[Any],
    selected_skill: Optional[Any],
    tool_results_or_messages: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    text = str(user_text or "").lower()
    skill_name = str(getattr(active_skill_runtime, "skill_name", "") or getattr(selected_skill, "name", "")).lower()
    skill_desc = str(getattr(selected_skill, "description", "")).lower()
    compact_text = text.replace(" ", "")
    full_source_markers = (
        "全部 jira 信息", "所有 jira 信息", "完整 jira", "all jira information", "complete jira",
        "based on all jira", "generate all test cases from jira", "获取全部 jira 信息并生成测试用例",
        "全部jira信息", "所有jira信息", "完整jira", "基于全部jira", "全部confluence信息", "完整confluence",
        "all confluence", "complete confluence",
    )
    generation_markers = ("generate", "test", "code", "spec", "feature", "implementation", "review", "生成", "测试用例")
    has_jira_or_conf = bool(_extract_jira_key_or_url(text) or _extract_confluence_page_hint(text) or (" jira" in text) or ("confluence" in text))
    generation_intent = any(m in text for m in generation_markers) or any(m in skill_name for m in generation_markers) or any(m in skill_desc for m in generation_markers)
    explicit_full = any(m in text for m in full_source_markers) or any(m in compact_text for m in full_source_markers)
    return has_jira_or_conf and (explicit_full or generation_intent)


async def _recover_max_output_tokens(
    *,
    llm_result: Dict[str, Any],
    llm_kwargs: Dict[str, Any],
    loop_context_state: Optional[Dict[str, Any]],
    stage_hint: str,
) -> Tuple[Dict[str, Any], bool]:
    state = loop_context_state.setdefault("budget", {}) if isinstance(loop_context_state, dict) else {}
    recovered, info = await recover_max_output_tokens(
        llm_client=llm_client,
        llm_result=llm_result,
        llm_kwargs=llm_kwargs,
        state=state,
        stage=stage_hint,
    )
    if isinstance(state, dict):
        state["request_budget_stage"] = stage_hint
    return recovered, bool(info.get("applied"))


def degrade_projected_context_sources_in_responses_input_items(
    input_items: List[Dict[str, Any]],
    *,
    max_envelope_chars: int = 2500,
) -> List[Dict[str, Any]]:
    def _preserve_projected_structure(text: str) -> str:
        lines = str(text or "").splitlines()
        if not lines:
            return str(text or "")
        marker = lines[0].strip()
        head: List[str] = [lines[0]]
        if marker.startswith("[large source tool result projected]"):
            for line in lines[1:]:
                s = line.strip()
                if (
                    s.startswith("tool_name:")
                    or s.startswith("kind:")
                    or s.startswith("context_ref:")
                    or s.startswith("original_chars:")
                    or s.startswith("model_view_chars:")
                    or s.startswith("full_content_available:")
                ):
                    head.append(line)
            section_lines = [line for line in lines if line.strip().startswith("- ")]
            if section_lines:
                head.append("section_map:")
                head.extend(section_lines[:4])
        else:
            for line in lines[1:]:
                s = line.strip()
                if "ref=ctx://context/" in s or "ctx://context/" in s or s.startswith("Summary:"):
                    head.append(line)
                    break
            summary_lines = [line for line in lines if line.strip() and not line.startswith("[")]
            head.extend(summary_lines[:3])
        instruction_lines = [line for line in lines if "context_read_ref(" in line]
        if instruction_lines:
            head.append(instruction_lines[0])
        reduced = "\n".join(dict.fromkeys(head))
        return truncate_with_count(reduced, max_envelope_chars)

    degraded: List[Dict[str, Any]] = []
    marker_prefixes = (
        "[large source tool result projected]",
        "[old tool output compacted",
        "[old assistant output compacted",
        "[assistant tool-call content compacted",
        "[assistant skill content compacted",
    )
    for item in input_items or []:
        if not isinstance(item, dict):
            degraded.append(item)
            continue
        item_copy = dict(item)
        if item_copy.get("type") == "function_call_output":
            output_text = str(item_copy.get("output") or "")
            stripped = output_text.lstrip()
            if stripped.startswith(marker_prefixes) and "ctx://context/" in stripped and len(output_text) > max_envelope_chars:
                item_copy["output"] = _preserve_projected_structure(output_text)
        degraded.append(item_copy)
    return degraded


def build_responses_input_items(messages: List[Dict[str, Any]], *, session_id: Optional[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    tool_names_by_call_id: Dict[str, str] = {}

    def _feedback_output_for_message(msg: Dict[str, Any], content: Any) -> str:
        if not content:
            return ""
        call_id = msg.get("tool_call_id", "")
        tool_name = (
            msg.get("tool_name")
            or msg.get("name")
            or tool_names_by_call_id.get(call_id)
        )
        return _tool_feedback_text_for_tool(
            tool_name,
            content,
            session_id=session_id,
            source_id=call_id or tool_name,
        )

    for msg in messages:
        role = msg.get("role", "user")

        tool_call_id = msg.get("tool_call_id", "")
        if tool_call_id and role == "tool":
            content = msg.get("content", "")
            items.append({
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": _feedback_output_for_message(msg, content),
            })
            continue

        if role == "tool":
            continue

        tool_calls = msg.get("tool_calls", [])
        if tool_calls and role == "assistant":
            content = msg.get("content", "")
            if content:
                if isinstance(content, list):
                    items.append({"role": role, "content": content})
                else:
                    content_text = str(content)
                    if _is_projected_feedback(content_text):
                        items.append({"role": role, "content": content_text})
                    elif _should_project_assistant_context(content_text):
                        content_text = project_skill_assistant_content_for_input_items(
                            content_text,
                            session_id=session_id,
                            round_num=0,
                        )
                        items.append({"role": role, "content": content_text})
                    else:
                        items.append({"role": role, "content": content_text})
            for tc in tool_calls:
                call_id = tc.get("id", "")
                func = tc.get("function", {})
                name = func.get("name", "")
                args = func.get("arguments", {})
                args_str = args if isinstance(args, str) else json.dumps(args)
                if call_id and name:
                    tool_names_by_call_id[call_id] = name
                items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": args_str,
                })
            continue

        if tool_call_id:
            content = msg.get("content", "")
            items.append({
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": _feedback_output_for_message(msg, content),
            })
            continue

        content = msg.get("content", "")
        if isinstance(content, list):
            conv = []
            for value in content:
                if isinstance(value, dict):
                    t = value.get("type", "")
                    if t in ("text", "input_text"):
                        if role == "user":
                            conv.append({"type": "input_text", "text": value.get("text", "")})
                        else:
                            conv.append(value.get("text", ""))
                    elif t in ("image_url", "input_image"):
                        img = value.get("image_url", {})
                        img_url = img.get("url") if isinstance(img, dict) else str(img)
                        if img_url:
                            conv.append({"type": "input_image", "image_url": img_url})
                    else:
                        conv.append(value)
                else:
                    conv.append({"type": "input_text", "text": str(value)} if role == "user" else str(value))
            if conv:
                items.append({"role": role, "content": conv})
        elif content:
            if role == "user":
                items.append({"role": role, "content": [{"type": "input_text", "text": str(content)}]})
            else:
                content_text = str(content)
                if role == "assistant":
                    if _is_projected_feedback(content_text):
                        items.append({"role": role, "content": content_text})
                    elif _should_project_assistant_context(content_text):
                        content_text = project_skill_assistant_content_for_input_items(
                            content_text,
                            session_id=session_id,
                            round_num=0,
                        )
                        items.append({"role": role, "content": content_text})
                    else:
                        items.append({"role": role, "content": content_text})
                else:
                    items.append({"role": role, "content": content_text})

    return items


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled (logger is DEBUG level)."""
    global _DEBUG_ENABLED
    if _DEBUG_ENABLED is None:
        _DEBUG_ENABLED = logger.isEnabledFor(logging.DEBUG)
    return _DEBUG_ENABLED


def _format_content(content: str, prefix: str = "", max_length: int = 500) -> str:
    """Format content for logging. Truncated when debug is enabled, hidden when disabled."""
    if not content:
        return f"{prefix}(empty)"
    if _is_debug_enabled():
        # Debug enabled: show truncated content for readability
        if len(content) > max_length:
            return f"{prefix}{content[:max_length]}... [{len(content) - max_length} chars truncated]"
        return f"{prefix}{content}"
    # Debug disabled: don't log content at all
    return f"{prefix}(content hidden)"


def _inject_attached_images_into_last_user_message(
    messages: List[Dict[str, Any]],
    attached_images: Optional[List[str]],
) -> int:
    if not attached_images or not messages:
        return 0

    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") != "user":
            continue

        user_content = messages[i].get("content", "")
        if isinstance(user_content, list):
            msg_content = []
            for item in user_content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        msg_content.append({"type": "input_text", "text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        img_url = item.get("image_url", {}).get("url") if isinstance(item.get("image_url"), dict) else str(item.get("image_url", ""))
                        if img_url:
                            msg_content.append({"type": "input_image", "image_url": img_url})
                    else:
                        msg_content.append(item)
        else:
            msg_content = [{"type": "input_text", "text": str(user_content)}]

        existing_image_urls = {
            block.get("image_url")
            for block in msg_content
            if isinstance(block, dict) and block.get("type") == "input_image" and block.get("image_url")
        }
        added_count = 0
        for img in attached_images:
            if img in existing_image_urls:
                continue
            msg_content.append({"type": "input_image", "image_url": img})
            existing_image_urls.add(img)
            added_count += 1

        messages[i] = {"role": "user", "content": msg_content}
        return added_count

    return 0


def _hash_text(value: Any, max_len: int = 800) -> str:
    text = str(value or "")
    if len(text) > max_len:
        text = text[:max_len]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _normalize_tool_args(args_str: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(args_str) if isinstance(args_str, str) and args_str.strip() else {}
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {"raw": str(parsed)}


def _build_progress_signature(
    skill_session: SkillSession,
    normalized_args: Dict[str, Any],
    output_text: str,
) -> Dict[str, str]:
    last_step = skill_session.completed_steps[-1] if skill_session.completed_steps else {}
    last_step_summary = f"{last_step.get('type', '')}:{str(last_step.get('result', ''))[:240]}"
    args_sig = _hash_text(json.dumps(normalized_args, sort_keys=True, ensure_ascii=False), max_len=500)
    output_sig = _hash_text(output_text, max_len=1000)
    artifacts_sig = _hash_text(json.dumps(skill_session.artifacts or {}, sort_keys=True, ensure_ascii=False), max_len=1000)
    step_sig = _hash_text(last_step_summary, max_len=400)
    state_signature = "|".join([output_sig, artifacts_sig, step_sig])
    return {
        "state_signature": state_signature,
        "args_signature": args_sig,
        "output_signature": output_sig,
    }


def _is_lookup_only_skill(skill: Any, skill_session: SkillSession, message: str) -> bool:
    lookup_words = ("get", "list", "query", "search", "read", "fetch", "show", "find", "issue", "file", "info")
    generate_words = ("generate", "create", "write", "modify", "output", "testcase", "code", "doc", "scaffold", "produce")
    haystack = " ".join([
        str(getattr(skill, "name", "") or ""),
        str(getattr(skill, "description", "") or ""),
        str(skill_session.goal or ""),
        str(message or ""),
    ]).lower()
    tokens = re.findall(r"[a-z0-9_]+", haystack)
    if any(word in tokens for word in generate_words):
        return False
    return any(word in tokens for word in lookup_words)


def _attach_governance_hint(tool_result: ToolResult, governance_payload: Dict[str, Any]) -> ToolResult:
    # Phase 2 compatibility bridge:
    # GovernanceBus remains the policy decision source.
    # This metadata on ToolResult is only a transitional carrier for legacy
    # agent-loop hint consumption; avoid direct getattr/setattr usage elsewhere.
    """Attach governance metadata onto ToolResult in one explicit bridge helper."""
    payload = governance_payload if isinstance(governance_payload, dict) else {}
    setattr(tool_result, _TOOL_RESULT_GOVERNANCE_ATTR, dict(payload))
    return tool_result


def _read_governance_hint(tool_result: ToolResult) -> Dict[str, Any]:
    # Phase 2 compatibility bridge:
    # GovernanceBus computes policy hints; this accessor only reads the
    # transitional ToolResult metadata shape for legacy loop decisions.
    # Keep access centralized to avoid implicit contract drift.
    """Read governance metadata from ToolResult and always return a dict."""
    payload = getattr(tool_result, _TOOL_RESULT_GOVERNANCE_ATTR, {})
    if isinstance(payload, dict):
        return dict(payload)
    return {}


async def _execute_tool_via_runtime_bus(
    *,
    session_id: str,
    tool_name: str,
    args: Dict[str, Any],
    runtime_config: Optional[SkillRuntimeConfig] = None,
    event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    source_ref: str = "agents.core",
) -> ToolResult:
    """Phase 1 compatibility helper to route tool/task execution through ExecutionBus.

    NOTE: this bridge keeps legacy monkeypatch/patch-point behavior while routing through the bus.
    """
    use_task_boundary = bool(runtime_config and tool_name in set(runtime_config.task_tools))
    execution_type = "task" if use_task_boundary else "tool"
    input_payload: Dict[str, Any]
    if use_task_boundary:
        input_payload = {
            "task_type": "tool_task",
            "tool_name": tool_name,
            "kwargs": dict(args),
            "event_callback": event_callback,
        }
    else:
        input_payload = {"tool_name": tool_name, "kwargs": dict(args)}

    result = await execute_tool_or_task_orchestration(
        source_type="agent",
        source_ref=source_ref,
        execution_type=execution_type,
        session_id=session_id,
        input_payload=input_payload,
        metadata={"tool_name": tool_name, "task_boundary": use_task_boundary},
        execute_tool_func=execute_tool_by_name,
    )
    payload: Dict[str, Any] = result.output_payload if isinstance(result.output_payload, dict) else {"value": result.output_payload}
    explicit_success = payload.get("success")
    if isinstance(explicit_success, bool):
        payload_success = explicit_success
    elif payload.get("error"):
        payload_success = False
    else:
        payload_success = True
    success = result.status not in {"error", "blocked"} and payload_success
    content = payload.get("content")
    if content is None:
        content = payload.get("output")
    if content is None:
        content = payload.get("response")
    if content is None:
        content = payload.get("value")
    if content is None and payload.get("error"):
        content = f"Error: {payload.get('error')}"
    if content is None:
        content = ""
    error = payload.get("error")
    if error is None and result.status == "blocked":
        error = "Execution blocked"
    tool_result = ToolResult(
        success=success,
        content=str(content),
        error=error,
    )
    governance_artifacts = result.artifacts if isinstance(result.artifacts, dict) else {}
    governance_payload = governance_artifacts.get("governance") if isinstance(governance_artifacts.get("governance"), dict) else {}
    return _attach_governance_hint(tool_result, governance_payload)


@dataclass
class SkillTurnState:
    round_index: int = 0
    llm_call_count: int = 0
    tool_round_count: int = 0
    transition: str = "tool_followup"
    has_function_calls: bool = False
    has_readonly_success: bool = False
    has_write_call: bool = False
    lookup_only_hint: bool = False


@dataclass
class FinalizerResult:
    state: str
    attempts: int
    parsed_action: str
    raw_output: str
    termination_reason: str
    fallback_used: bool = False
    request_budget: Dict[str, Any] = field(default_factory=dict)


def _is_tool_allowed_by_skill_runtime(tool_name: str, runtime_config: Optional[SkillRuntimeConfig]) -> bool:
    if not runtime_config or not runtime_config.tool_policy_declared:
        return True
    lowered_tool = str(tool_name or "").strip().lower()
    if not lowered_tool:
        return False
    allowed = {str(name).strip().lower() for name in (runtime_config.allowed_tools_set or set()) if str(name).strip()}
    allowed.update({name.lower() for name in INTERNAL_SUPPORT_TOOL_NAMES})
    return lowered_tool in allowed


def _evaluate_skill_progress(
    *,
    skill_session: SkillSession,
    tool_name: str,
    normalized_args: Dict[str, Any],
    output_text: str,
) -> Dict[str, Any]:
    progress_data = _build_progress_signature(
        skill_session=skill_session,
        normalized_args=normalized_args,
        output_text=output_text,
    )
    progressed = progress_data["state_signature"] != skill_session.last_progress_signature
    if progressed:
        reason = "progressed"
    elif skill_session.last_tool_name == tool_name and skill_session.last_tool_args_signature == progress_data["args_signature"] and skill_session.last_tool_output_signature == progress_data["output_signature"]:
        reason = "repeated_same_tool_output"
    else:
        reason = "no_state_delta"
    return {
        "progressed": progressed,
        "reason": reason,
        "state_signature": progress_data["state_signature"],
        "args_signature": progress_data["args_signature"],
        "output_signature": progress_data["output_signature"],
    }


async def _run_skill_finalizer(
    *,
    input_items: List[Dict[str, Any]],
    system_prompt: str,
    provider: str,
    model: Optional[str],
    skill_session: SkillSession,
    track_usage: bool,
    usage_data: Dict[str, Any],
    remaining_llm_budget: int,
    max_tokens: Optional[int] = None,
) -> tuple[FinalizerResult, Dict[str, Any]]:
    raw_output = ""
    fallback_used = False
    parsed_action = "execute"
    termination_reason = "finalizer_terminal_failed"
    finalizer_request_budget: Dict[str, Any] = {}
    max_attempts = min(2, max(0, remaining_llm_budget))
    for attempt in range(max_attempts):
        skill_session.finalizer_state = "running"
        skill_session.finalizer_attempts += 1
        logger.info("[SkillMode][Finalizer] state=running attempt=%s", skill_session.finalizer_attempts)
        prompt = "Do not call tools. Return exactly one control marker on the first line: [FINISH] or [ASK_USER]."
        if attempt == 1:
            prompt += " STRICT: marker must be first line and body must be plain text."
        items = list(input_items)
        items.append({"role": "user", "content": [{"type": "input_text", "text": prompt}]})
        loop_budget = resolve_prompt_budget(stage="skill_generation", model=model)
        output_boundary = resolve_output_boundary(model)
        prompt_budget_tokens = int(loop_budget.get("prompt_budget_tokens", 0) or 0)
        model_max = int(loop_budget.get("max_output_tokens") or 64000)
        effective_max_tokens, max_token_diag = _resolve_effective_max_tokens(model_max)
        requested_max = int(max_tokens or effective_max_tokens)
        budget_max_tokens = min(requested_max, effective_max_tokens)
        items_for_call = items
        request_estimated_tokens = estimate_llm_request_tokens(
            input_items=items_for_call,
            system_prompt=system_prompt or "",
            tools=[],
        )
        finalizer_request_budget = {
            "request_estimated_tokens": request_estimated_tokens,
            "prompt_budget_tokens": prompt_budget_tokens,
            "reserved_output_tokens": min(int(loop_budget.get("reserved_output_tokens", 0) or 0), budget_max_tokens),
            "safety_margin_tokens": loop_budget.get("safety_margin_tokens"),
            "max_prompt_tokens": loop_budget.get("max_prompt_tokens"),
            "max_output_tokens": budget_max_tokens,
            "configured_max_tokens": max_token_diag.get("configured_max_tokens"),
            "effective_max_tokens": max_token_diag.get("effective_max_tokens"),
            "legacy_max_tokens_ignored": bool(max_token_diag.get("legacy_max_tokens_ignored")),
            "request_over_budget": request_estimated_tokens > prompt_budget_tokens if prompt_budget_tokens else False,
            "request_budget_stage": "skill_finalizer",
            "stage": "skill_finalizer",
        }
        if request_estimated_tokens > prompt_budget_tokens:
            items_for_call = degrade_projected_context_sources_in_responses_input_items(items_for_call)
            request_estimated_tokens = estimate_llm_request_tokens(
                input_items=items_for_call,
                system_prompt=system_prompt or "",
                tools=[],
            )
            finalizer_request_budget["request_estimated_tokens"] = request_estimated_tokens
            finalizer_request_budget["request_over_budget"] = request_estimated_tokens > prompt_budget_tokens if prompt_budget_tokens else False
        if _should_abort_over_budget(request_estimated_tokens, prompt_budget_tokens):
            skill_session.finalizer_state = "terminal_failed"
            logger.warning("[SkillMode][Finalizer] state=terminal_failed reason=finalizer_context_budget_exceeded")
            return (
                FinalizerResult(
                    "terminal_failed",
                    skill_session.finalizer_attempts,
                    "ask_user",
                    "[ASK_USER]\nI need to continue in smaller steps because the context is too large to finalize safely.",
                    "finalizer_context_budget_exceeded",
                    True,
                    finalizer_request_budget,
                ),
                usage_data,
            )
        result, _output_diag = await call_llm_with_output_control(
            llm_client=llm_client,
            llm_kwargs={
                "input_items": items_for_call,
                "system_prompt": system_prompt,
                "tools": None,
                "reasoning_replay": False,
                "provider": _normalize_provider_key(provider),
                "max_tokens": budget_max_tokens,
                **({"model": model} if model else {}),
            },
            session_id=getattr(skill_session, "session_id", None) or "unknown_session",
            stage="skill_finalizer",
            context_state={"budget": finalizer_request_budget},
            active_skill=None,
            latest_user_text="",
            max_chat_output_chars=None,
        )
        skill_session.llm_call_count += 1
        if not result.get("error"):
            if track_usage:
                iter_usage = result.get("usage", {}) or {}
                usage_data = {
                    "prompt_tokens": usage_data.get("prompt_tokens", 0) + iter_usage.get("prompt_tokens", 0),
                    "completion_tokens": usage_data.get("completion_tokens", 0) + iter_usage.get("completion_tokens", 0),
                    "total_tokens": usage_data.get("total_tokens", 0) + iter_usage.get("total_tokens", 0),
                }
            candidate = (result.get("content") or "").strip()
            parsed_action, _ = _parse_skill_control_marker(candidate)
            if parsed_action in {"ask_user", "finish"}:
                skill_session.finalizer_state = "succeeded"
                termination_reason = "finalizer_succeeded"
                raw_output = candidate
                logger.info("[SkillMode][Finalizer] state=succeeded")
                return FinalizerResult(
                    "succeeded",
                    skill_session.finalizer_attempts,
                    parsed_action,
                    raw_output,
                    termination_reason,
                    False,
                    finalizer_request_budget,
                ), usage_data
        skill_session.finalizer_state = "retryable_failed" if skill_session.finalizer_attempts < 2 else "terminal_failed"
        logger.warning("[SkillMode][Finalizer] state=%s", skill_session.finalizer_state)
    skill_session.finalizer_state = "terminal_failed"
    fallback_used = True
    if skill_session.completed_steps:
        summary = "; ".join(str(step.get("result", ""))[:120] for step in skill_session.completed_steps[-3:] if step.get("result")) or "Skill execution reached a stable stopping point."
        raw_output = f"[FINISH]\n{summary}"
    elif skill_session.pending_question:
        raw_output = f"[ASK_USER]\n{skill_session.pending_question}"
    else:
        raw_output = "[FINISH]\nSkill execution completed with fallback summary."
    parsed_action, _ = _parse_skill_control_marker(raw_output)
    return FinalizerResult(
        "terminal_failed",
        skill_session.finalizer_attempts,
        parsed_action,
        raw_output,
        termination_reason,
        fallback_used,
        finalizer_request_budget,
    ), usage_data


class Agent:
    """Agent for processing messages with ReAct pattern (Reasoning + Acting)."""

    def __init__(
        self, 
        system_prompt: Optional[str] = None, 
        session_id: str = "default",
        think_level: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ):
        # Resolve thinking level
        self.think_level = normalize_think_level(think_level) or ThinkLevel.OFF
        
        # Store model for later use
        self.model = model
        self.agent_id = agent_id
        self.agent_name = agent_name
        
        # Initialize heartbeat if enabled
        self._heartbeat_enabled = config.heartbeat.get("enabled", False)
        if self._heartbeat_enabled:
            check_interval = config.heartbeat.get("check_interval", 300)
            self._heartbeat = get_heartbeat(self.think_level)
            # Set the check interval from config
            self._heartbeat.check_interval = check_interval
            logger.info(f"Heartbeat enabled - think_level={self.think_level.value}, interval={check_interval}s")
        
        # Initialize Memory Update Manager for auto-memory
        self.memory_update_manager = MemoryUpdateManager(
            workspace=str(memory_system.workspace),
            llm_client=llm_client,
            memory_system=memory_system,
        )
        
        # Build Engineering Flow Platform-style system prompt
        # NOTE: get_tools_schema() already includes INTEGRATION_TOOLS (JIRA + Confluence + GitHub tools)
        full_tool_catalog = get_tools_schemas()
        llm_tool_filter_result = filter_tool_schemas_for_llm(full_tool_catalog, config.llm or {})
        self.tools = llm_tool_filter_result.filtered_schemas
        self.allowed_tool_names = set(llm_tool_filter_result.allowed_tool_names)

        logger.info(
            "[Tool Policy] Initialized llm.tools filter: full_count=%s filtered_count=%s mode=%s configured=%s unmatched_patterns=%s",
            len(full_tool_catalog),
            len(self.tools),
            llm_tool_filter_result.mode,
            llm_tool_filter_result.configured,
            llm_tool_filter_result.unmatched_patterns,
        )
        logger.debug(
            "Tools initialized: count=%s names=%s think_level=%s",
            len(self.tools),
            [extract_tool_name(t) for t in self.tools if extract_tool_name(t)],
            self.think_level.value,
        )

        # Human-readable tool list
        if self.tools:
            tools_list = "\n".join([
                f"- **{(extract_tool_name(t) or 'unknown_tool')}**: {t.get('function', {}).get('description') or t.get('description', '')}"
                for t in self.tools
            ])
        else:
            tools_list = "No runtime tools are enabled for this agent by llm.tools policy."
        
        # Load memory files for system prompt
        # For main session (includes memory), include MEMORY.md
        # For other sessions, exclude memory for security
        self.include_memory = (session_id == "main" or session_id.startswith("main") or 
                         session_id.startswith("webchat"))
        
        memory_prompt = memory_system.build_system_prompt(include_memory=self.include_memory)
        
        # Current date/time for the prompt
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Build runtime information
        runtime_info = format_runtime_info(
            host="engineering-flow-platform",
            os_info=f"{platform.system()} {platform.release()}",
            arch=platform.machine(),
            node=platform.python_version(),
            model=self.model or "",
            default_model="",
            channel="",
            capabilities=[],
            think_level=self.think_level,
        )
        
        if system_prompt:
            # Custom prompt provided
            self.system_prompt = system_prompt
            prompt_source = "custom"
        elif memory_prompt:
            # Use memory files + basic structure
            self.system_prompt = f"""{memory_prompt}

## Tooling

You have access to the following tools. When a user asks you to do something that requires a tool, you MUST use the appropriate tool. Do NOT explain how to do something—DO IT directly.

{tools_list}

## Runtime
{runtime_info}

## Current Date & Time
{current_time}
"""
            prompt_source = "memory"
        else:
            # Fallback to basic prompt
            self.system_prompt = f"""You are a helpful AI assistant that can execute commands, read/write files, and more.

## Tooling

You have access to the following tools. When a user asks you to do something that requires a tool, you MUST use the appropriate tool. Do NOT explain how to do something—DO IT directly.

{tools_list}

## Runtime
{runtime_info}

## Guidelines

- When a user asks to run a command → use the exec tool
- When a user asks to read a file → use the read tool
- When a user asks to write/edit a file → use the write/edit tool
- Execute tools proactively—don't just talk about actions

## Current Date & Time
{current_time}
"""
            prompt_source = "fallback"
        
        # Debug logging for system prompt construction
        logger.debug(f"System prompt constructed: session={session_id}, "
                    f"include_memory={self.include_memory}, source={prompt_source}, "
                    f"length={len(self.system_prompt)}, tools={len(self.tools)}, "
                    f"think_level={self.think_level.value}")

    def _build_user_author_extra(
        self,
        portal_user_id: Optional[str],
        portal_user_name: Optional[str],
        user_name: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "author_type": "human",
            "author_id": portal_user_id or portal_user_name or user_name or "unknown",
            "author_name": portal_user_name or user_name or "User",
            "author_source": "portal" if (portal_user_id or portal_user_name) else "runtime",
        }

    def _build_agent_author_extra(self) -> Dict[str, Any]:
        extra: Dict[str, Any] = {
            "author_type": "agent",
            "author_name": getattr(self, "agent_name", None) or "Assistant",
            "author_source": "runtime",
        }
        agent_id = getattr(self, "agent_id", None)
        if agent_id:
            extra["author_id"] = agent_id
        return extra

    def _build_assistant_message_extra(
        self,
        content: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        merged_extra: Dict[str, Any] = self._build_agent_author_extra()
        supplied_extra = dict(extra or {})
        raw_display_blocks = supplied_extra.get("display_blocks")
        merged_extra["display_blocks"] = normalize_display_blocks(raw_display_blocks, content)
        merged_extra.update(supplied_extra)
        merged_extra["display_blocks"] = normalize_display_blocks(
            merged_extra.get("display_blocks"),
            content,
        )
        return merged_extra

    def _build_assistant_result_payload(
        self,
        content: str,
        *,
        usage: Optional[Dict[str, Any]] = None,
        user_message_id: Optional[str] = None,
        events: Optional[List[Dict[str, Any]]] = None,
        reasoning: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        include_content_alias: bool = False,
        include_role: bool = False,
        **additional_fields: Any,
    ) -> Dict[str, Any]:
        assistant_extra = self._build_assistant_message_extra(content, extra)
        payload: Dict[str, Any] = {
            "response": content,
            "display_blocks": assistant_extra.get("display_blocks", []),
        }
        for key in ("author_type", "author_id", "author_name", "author_source"):
            if key in assistant_extra:
                payload[key] = assistant_extra[key]
        if usage is not None:
            payload["usage"] = usage
        if user_message_id:
            payload["user_message_id"] = user_message_id
        if events:
            payload["events"] = events
        if reasoning:
            payload["reasoning"] = reasoning
        if include_content_alias:
            payload["content"] = content
        if include_role:
            payload["role"] = "assistant"
        payload.update(additional_fields)
        return payload

    async def _persist_assistant_message(
        self,
        session_id: str,
        content: str,
        *,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        assistant_extra = self._build_assistant_message_extra(content, extra)
        await session_manager.add_message(
            session_id,
            "assistant",
            content,
            extra=assistant_extra,
        )
        return assistant_extra

    def _assistant_extra_from_payload(
        self,
        payload: Optional[Dict[str, Any]],
        content: str,
    ) -> Dict[str, Any]:
        assistant_extra: Dict[str, Any] = {}
        if isinstance(payload, dict) and "display_blocks" in payload:
            assistant_extra["display_blocks"] = payload.get("display_blocks")
        elif payload is not None and hasattr(payload, "display_blocks"):
            assistant_extra["display_blocks"] = getattr(payload, "display_blocks")
        return self._build_assistant_message_extra(content, assistant_extra)

    async def process(
        self,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
        track_usage: bool = True,
        reasoning_replay: Optional[bool] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        attached_images: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
        portal_user_id: Optional[str] = None,
        portal_user_name: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process a user message with ReAct pattern.
        
        Flow: User → Fast Lane Commands → LLM (with tools) → Tool Call → Execute → Result → LLM → Final Response
        
        Args:
            reasoning_replay: Enable reasoning_replay to see model's internal reasoning.
                When enabled, includes model's thinking process in response.
                Default: Uses config.llm.reasoning_replay setting.
            stream_callback: Optional callback for streaming events (tool calls, progress, etc.)
            portal_user_id: Optional portal-originated user ID used for persisted user-message
                author metadata. Falls back to runtime/user_name identity when absent.
            portal_user_name: Optional portal-originated display name used for persisted
                user-message author metadata. Falls back to runtime/user_name when absent.
        
        Returns:
            Dict with:
                - response: str - The assistant's response
                - reasoning: str - Model's internal reasoning (if reasoning_replay enabled)
                - usage: Dict - Token usage from LLM API (if track_usage=True)
        """
        usage_data = {}
        
        # Add user message to history (with attachments if any)
        extra = self._build_user_author_extra(portal_user_id, portal_user_name, user_name)
        if attachments:
            extra["attachments"] = attachments  # Save file IDs, not base64
        user_message_id = await session_manager.add_message(
            session_id, "user", message,
            extra=extra
        )

        def emit_early_runtime_event(event_type: str, event_data: Dict[str, Any]) -> None:
            try:
                from src.gateway.event_bus import emit_agent_event_sync
                emit_agent_event_sync(event_type, event_data)
            except Exception as event_error:
                logger.debug(f"Failed to emit early runtime event to event bus: {event_error}")

            if not stream_callback:
                return

            try:
                event = json.dumps({"type": event_type, **event_data}, default=str)
                if hasattr(stream_callback, "put"):
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(stream_callback.put(event))
                        else:
                            stream_callback.put_nowait(event)
                    except RuntimeError:
                        stream_callback.put_nowait(event)
                else:
                    stream_callback(event)
            except Exception as event_error:
                logger.debug(f"Failed to emit early runtime event to stream callback: {event_error}")

        def build_early_result(
            content: str,
            *,
            event_type: str = "execution.completed",
            state: str = "completed",
            event_data: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            enriched_event = _enrich_runtime_event_context(
                {
                    "message": content,
                    "state": state,
                    **(event_data or {}),
                },
                session_id=session_id,
                agent_id=self.agent_id,
                request_id=request_id,
            )
            result = self._build_assistant_result_payload(
                content,
                usage=usage_data,
                user_message_id=user_message_id,
            )
            if request_id:
                result.setdefault("request_id", request_id)
            emit_early_runtime_event(event_type, enriched_event)
            result["runtime_events"] = [_build_runtime_event_record(event_type, enriched_event)]
            return result

        # Get conversation history
        messages = await session_manager.get_history(session_id)
        
        # DEBUG: Log raw history
        logger.debug(f"[{session_id}] Raw history count: {len(messages)}")
        
        # Transform history messages to ensure proper format for LLM
        # This handles tool messages that were saved with tool_call_id
        transformed_messages = []
        for msg in messages:
            metadata = msg.get("metadata") if isinstance(msg, dict) else None
            if isinstance(metadata, dict) and metadata.get("exclude_from_model_context"):
                continue
            if isinstance(msg, dict) and msg.get("exclude_from_model_context"):
                continue
            transformed = {"role": msg.get("role", "user"), "content": msg.get("content", "")}
            # Preserve tool_calls for assistant messages
            if msg.get("tool_calls"):
                transformed["tool_calls"] = msg["tool_calls"]
                logger.debug(f"[{session_id}] Found tool_calls in message: {msg.get('tool_calls')[0].get('id') if msg.get('tool_calls') else 'none'}")
            # Preserve tool_call_id for tool messages
            if msg.get("tool_call_id"):
                transformed["tool_call_id"] = msg["tool_call_id"]
                logger.debug(f"[{session_id}] Found tool_call_id in message: {msg.get('tool_call_id')}")
            transformed_messages.append(transformed)
        messages = transformed_messages
        
        logger.debug(f"[{session_id}] Transformed messages count: {len(messages)}")

        # ===== FAST LANE COMMANDS =====
        from src.agents.fastlane import process_fastlane_command
        
        fastlane_response = await process_fastlane_command(message, self)
        if fastlane_response:
            # Fast lane command processed, return the response
            await self._persist_assistant_message(session_id, fastlane_response)
            return build_early_result(
                fastlane_response,
                event_data={"reason": "fastlane_command"},
            )
        # ===== END FAST LANE =====

        # ===== SKILL MATCHING =====
        from src.skills import skill_registry

        # Initialize skill registry once
        if not skill_registry._initialized:
            skill_registry.load_skills()

        from src.skills import get_tracer

        existing_active_skill_contract = await session_manager.get_active_skill_session(session_id)
        
        def emit_skill_contract_cleared_event(previous_skill_name: str) -> None:
            if not previous_skill_name:
                return
            event_data = _enrich_runtime_event_context(
                {
                    "skill": previous_skill_name,
                    "status": "cleared",
                    "reason": "user_clear_command",
                },
                session_id=session_id,
                agent_id=self.agent_id,
                request_id=request_id,
            )
            try:
                from src.gateway.event_bus import emit_agent_event_sync

                emit_agent_event_sync("skill_contract_cleared", event_data)
            except Exception as event_error:
                logger.debug(f"Failed to emit skill_contract_cleared to event bus: {event_error}")
            if not stream_callback:
                return
            try:
                event = json.dumps({"type": "skill_contract_cleared", **event_data})
                if hasattr(stream_callback, "put"):
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(stream_callback.put(event))
                        else:
                            stream_callback.put_nowait(event)
                    except RuntimeError:
                        stream_callback.put_nowait(event)
                else:
                    stream_callback(event)
            except Exception as event_error:
                logger.debug(f"Failed to emit skill_contract_cleared to stream callback: {event_error}")

        if is_clear_active_skill_command(message):
            previous_skill_name = get_contract_skill_name(existing_active_skill_contract)
            await session_manager.set_active_skill_session(session_id, None)
            set_skill_workdir(None)
            emit_skill_contract_cleared_event(previous_skill_name)
            cleared_message = "Active skill cleared."
            await self._persist_assistant_message(session_id, cleared_message)
            return build_early_result(
                cleared_message,
                event_data={"reason": "skill_contract_cleared", "skill": previous_skill_name},
            )

        explicit_skill_name = parse_explicit_skill_switch_name(message)
        selected_skill = None
        activation_reason: Optional[str] = None
        active_skill_contract: Optional[Dict[str, Any]] = None
        if is_active_skill_contract_usable(existing_active_skill_contract):
            existing_skill_name = get_contract_skill_name(existing_active_skill_contract)
            if explicit_skill_name:
                explicit_skill = skill_registry.get_skill(explicit_skill_name)
                if explicit_skill and not getattr(explicit_skill, "deprecated", False):
                    selected_skill = explicit_skill
                    activation_reason = "switched" if explicit_skill.name != existing_skill_name else "continued"
                else:
                    not_found_message = f"Skill not found: {explicit_skill_name}"
                    await self._persist_assistant_message(session_id, not_found_message)
                    return build_early_result(
                        not_found_message,
                        state="failed",
                        event_type="execution.failed",
                        event_data={"reason": "skill_not_found", "skill": explicit_skill_name},
                    )
            else:
                continued_skill = skill_registry.get_skill(existing_skill_name)
                if continued_skill and not getattr(continued_skill, "deprecated", False):
                    selected_skill = continued_skill
                    activation_reason = "continued"
                else:
                    await session_manager.set_active_skill_session(session_id, None)
                    set_skill_workdir(None)

        matched_skills = []
        if selected_skill is None and explicit_skill_name:
            explicit_skill = skill_registry.get_skill(explicit_skill_name)
            if explicit_skill and not getattr(explicit_skill, "deprecated", False):
                selected_skill = explicit_skill
                activation_reason = "matched"
            else:
                not_found_message = f"Skill not found: {explicit_skill_name}"
                await self._persist_assistant_message(session_id, not_found_message)
                return build_early_result(
                    not_found_message,
                    state="failed",
                    event_type="execution.failed",
                    event_data={"reason": "skill_not_found", "skill": explicit_skill_name},
                )

        if selected_skill is None:
            matched_skills = skill_registry.match_skill(message)
            if matched_skills:
                selected_skill = matched_skills[0]
                activation_reason = "matched"

        # Start execution tracing
        tracer = get_tracer()
        execution_id = tracer.start_execution(
            session_id=session_id,
            user_message=message,
            matched_skill=selected_skill.name if selected_skill else None,
        )
        
        active_skill_runtime: Optional[SkillRuntimeConfig] = None

        if selected_skill:
            logger.info(f"[Skill] Active skill selected: {selected_skill.name} ({activation_reason})")
            
            # Set skill workdir for exec tool (async-safe via contextvars)
            set_skill_workdir(selected_skill.path or None)
            if selected_skill.path:
                logger.info(f"[Skill] Workdir: {selected_skill.path}")

            active_skill_runtime = skill_registry.get_skill_runtime_config(
                selected_skill,
                globally_allowed_tool_names=getattr(self, "allowed_tool_names", set()),
            )
            active_skill_contract = build_active_skill_contract(
                skill=selected_skill,
                runtime_config=active_skill_runtime,
                user_message=message,
                existing_contract=existing_active_skill_contract,
                activation_reason=activation_reason or "matched",
            )
            await session_manager.set_active_skill_session(session_id, active_skill_contract)
            if activation_reason in {"matched", "switched"}:
                tracer.log_tool_call(
                    tool_name="skill_matched",
                    arguments={"skill": selected_skill.name, "reason": activation_reason},
                    result=f"Matched skill: {selected_skill.name}",
                )
            else:
                tracer.log_tool_call(
                    tool_name="skill_contract_continued",
                    arguments={"skill": selected_skill.name, "reason": activation_reason},
                    result=f"Continued active skill contract: {selected_skill.name}",
                )
        else:
            set_skill_workdir(None)
        # ===== END SKILL MATCHING =====

        # ===== MESSAGE COMPACTION =====
        model = self.model or config.llm.get("model", "gpt-5-mini")
        messages, pre_request_context_state = await prepare_progressive_messages(
            messages=messages,
            model=model,
            session_id=session_id,
            stage="pre_request",
            recent_count=5,
        )
        # ===== END MESSAGE COMPACTION =====

        # Debug logging for message received
        if _is_debug_enabled():
            logger.debug(f"=== [AGENT] MESSAGE RECEIVED ===")
            logger.debug(f"Session: {session_id}")
            logger.debug(f"User: {user_name}")
            logger.debug(f"Message length: {len(message)} chars")
            logger.debug(f"Message preview: {_format_content(message, max_length=300)}")
            logger.debug(f"System prompt length: {len(self.system_prompt)} chars")
            logger.debug(f"System prompt preview: {_format_content(self.system_prompt, max_length=300)}")
            logger.debug(f"Tools count: {len(self.tools)}")
            logger.debug(f"History messages: {len(messages)}")

        # ===== REACT PATTERN =====

        # Log thinking level for subagent tracking
        logger.info(f"[{session_id}] think_level={self.think_level.value}, model={self.model or ''}")
        
        # Resolve reasoning_replay from config if not provided
        enable_reasoning = reasoning_replay if reasoning_replay is not None else config.llm.get('reasoning_replay', False)
        logger.info(f"[{session_id}] reasoning_replay={enable_reasoning}")
        
        # ===== BUILD EFFECTIVE PROMPT ASSEMBLY (layered skill runtime + semantic context) =====
        prompt_assembly = get_effective_skill_runtime_prompt(
            base_system_prompt=self.system_prompt,
            runtime_config=active_skill_runtime,
        )
        effective_system_prompt, prompt_boundary_mode = resolve_prompt_execution_boundary(prompt_assembly)
        reference_attachment = get_skill_reference_attachment(active_skill_runtime)

        if active_skill_runtime:
            logger.info(f"[Skill] Applied layered prompt assembly for: {active_skill_runtime.skill_name}")
        
        # Semantic Context Search - Find relevant memory context
        semantic_context = ""
        try:
            semantic_context = memory_system.build_context_with_search(
                query=message,
                include_memory=self.include_memory,
                limit=3,
                score_threshold=0.3,
            )
            if semantic_context:
                effective_system_prompt = f"{effective_system_prompt}\n\n## Relevant Context (Semantic Search)\n\n{semantic_context}"
                logger.info(f"[Memory] Added semantic context from search")
        except Exception as e:
            logger.debug(f"[Memory] Semantic search failed: {e}")
        
        # ===== TOOL LOOP (REACT Pattern) =====
        # Continue calling LLM until it stops requesting tools
        # This is the proper agent loop, not a single-step execution
        
        # Get max iterations from config, default to 30
        max_tool_iterations = config.session.get("max_iterations", 30) if hasattr(config, 'session') else 30
        
        iteration = 0
        runtime_events_for_result: List[Dict[str, Any]] = []
        latest_request_budget: Dict[str, Any] = {}
        
        # Helper function to send stream events
        # Supports both simple callbacks and asyncio.Queue
        def send_event(event_type: str, data: dict):
            """Send event via stream_callback and event bus."""
            event_data = _enrich_runtime_event_context(
                data,
                session_id=session_id,
                agent_id=self.agent_id,
                request_id=request_id,
                task_id=data.get("task_id"),
                group_id=data.get("group_id") or data.get("portal_group_id"),
                coordination_run_id=data.get("coordination_run_id") or data.get("portal_coordination_run_id"),
            )
            runtime_events_for_result.append(_build_runtime_event_record(event_type, event_data))
            # Also log to tracer for persistence
            if event_type == 'llm_thinking':
                try:
                    from src.skills import get_tracer
                    tracer_instance = get_tracer()
                    message = event_data.get('message', '')
                    if message:
                        tracer_instance.log_thinking(message)
                except Exception:
                    pass  # Tracer may not be initialized

            # Emit to event bus for WebSocket clients
            try:
                from src.gateway.event_bus import emit_agent_event_sync
                emit_agent_event_sync(event_type, event_data)
            except Exception as e:
                logger.info(f"Event bus emit error: {e}")

            # Also send via callback if provided
            if stream_callback:
                import json
                event = json.dumps({"type": event_type, **event_data})
                try:
                    # Check if it's an asyncio.Queue
                    if hasattr(stream_callback, 'put'):
                        # It's a queue - put the event (will be read by API)
                        import asyncio
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                # We're in an async context, schedule the put
                                asyncio.create_task(stream_callback.put(event))
                            else:
                                # Loop not running, put directly
                                stream_callback.put_nowait(event)
                        except RuntimeError:
                            stream_callback.put_nowait(event)
                    else:
                        # Regular callback
                        stream_callback(event)
                except Exception as e:
                    logger.debug(f"Stream event error: {e}")

        def attach_runtime_events(payload: Dict[str, Any]) -> Dict[str, Any]:
            if isinstance(payload, dict):
                payload["runtime_events"] = list(runtime_events_for_result)
                if latest_request_budget:
                    payload["request_budget"] = dict(latest_request_budget)
            return payload

        def emit_context_snapshot(
            stage: str,
            context_state: Optional[Dict[str, Any]],
            *,
            iteration: Optional[int] = None,
        ) -> None:
            if not isinstance(context_state, dict) or not context_state:
                return
            budget = context_state.get("budget") if isinstance(context_state.get("budget"), dict) else {}
            payload: Dict[str, Any] = {
                "stage": stage,
                "context_state": context_state,
                "budget": budget,
            }
            if iteration is not None:
                payload["iteration"] = iteration
            send_event("context_snapshot", payload)

            compaction_level = str(context_state.get("compaction_level") or "").lower()
            next_action = str(budget.get("next_compaction_action") or "")
            if next_action == "approaching_micro_compaction" and compaction_level not in {"micro", "full"}:
                send_event(
                    "context_compaction_planned",
                    {
                        "stage": stage,
                        "iteration": iteration,
                        "compaction_level": "micro",
                        "budget": budget,
                        "next_pruning_policy": budget.get("next_pruning_policy"),
                        "message": "Context is approaching micro-compaction threshold.",
                    },
                )

            if compaction_level in {"micro", "full"}:
                send_event(
                    "context_compaction_applied",
                    {
                        "stage": stage,
                        "iteration": iteration,
                        "compaction_level": compaction_level,
                        "budget": budget,
                        "history_compacted_from_count": context_state.get("history_compacted_from_count"),
                        "history_compacted_to_count": context_state.get("history_compacted_to_count"),
                        "summary_source": context_state.get("summary_source"),
                    },
                )

        emit_context_snapshot("pre_request", pre_request_context_state)
        
        # Send skill matched event
        if selected_skill and activation_reason in {"matched", "switched"}:
            send_event("skill_matched", {"skill": selected_skill.name})
        if active_skill_contract:
            send_event(
                "skill_contract_active",
                {
                    "skill": active_skill_contract.get("skill_name"),
                    "status": active_skill_contract.get("status"),
                    "reason": active_skill_contract.get("activation_reason"),
                    "turn_count": active_skill_contract.get("turn_count"),
                    "skill_hash": active_skill_contract.get("skill_hash"),
                    "goal": active_skill_contract.get("goal") or active_skill_contract.get("original_user_request"),
                    "allowed_tools": (
                        active_skill_runtime.allowed_tools
                        if active_skill_runtime and isinstance(active_skill_runtime.allowed_tools, list)
                        else (active_skill_contract.get("allowed_tools") if isinstance(active_skill_contract.get("allowed_tools"), list) else [])
                    ),
                    "tool_policy_declared": (
                        active_skill_runtime.tool_policy_declared
                        if active_skill_runtime
                        else active_skill_contract.get("tool_policy_declared")
                    ),
                },
            )
        if active_skill_runtime:
            verbose_runtime_event = _is_debug_enabled() or bool(config.session.get("verbose_skill_runtime_events", False))
            send_event(
                "skill_runtime_applied",
                build_skill_runtime_event_payload(
                    runtime_config=active_skill_runtime,
                    reference_attachment=reference_attachment,
                    prompt_assembly=prompt_assembly,
                    prompt_boundary_mode=prompt_boundary_mode,
                    verbose=verbose_runtime_event,
                ),
            )
        
        added_images = _inject_attached_images_into_last_user_message(
            messages,
            attached_images,
        )
        if added_images:
            logger.info(
                "[Agent] Attached %s image(s) to user message (Responses format)",
                added_images,
            )

        input_items = build_responses_input_items(messages, session_id=session_id)
        
        # Keep track of messages for compaction during loop
        # IMPORTANT: Start fresh for each request to avoid carrying over
        # tool_calls and tool_results from previous requests/iterations.
        # loop_messages will be rebuilt as we go through the tool loop.
        loop_messages = messages.copy()
        loop_context_state = dict(pre_request_context_state or {})
        
        while iteration < max_tool_iterations:
            iteration += 1
            
            # ===== COMPACTION IN LOOP =====
            if iteration > 1:
                loop_messages, loop_context_state = await prepare_progressive_messages(
                    messages=loop_messages,
                    model=self.model or config.llm.get("model", "gpt-5-mini"),
                    session_id=session_id,
                    stage="tool_loop",
                    recent_count=5,
                )
                emit_context_snapshot("tool_loop", loop_context_state, iteration=iteration)
            input_items = build_responses_input_items(loop_messages, session_id=session_id)
            # ===== END COMPACTION IN LOOP =====
            
            # Send iteration start event
            send_event("iteration_start", {"iteration": iteration, "total": max_tool_iterations})
            
            # Step 1: Call LLM with tools (include skill_prompt from first call)
            logger.debug(f"[Tool Loop] Iteration {iteration}: Calling LLM")
            
            # Build context info for thinking display (without relying on model reasoning)
            context_info = []
            if iteration == 1:
                # Show user message on first iteration
                for item in input_items:
                    # Handle both formats: {'type': 'message', 'role': ...} or {'role': ..., 'content': ...}
                    role = item.get("role", "")
                    if role == "user":
                        content = item.get("content", "")
                        if isinstance(content, list):
                            text = " ".join([c.get("text", str(c)) for c in content])
                        else:
                            text = str(content)
                        context_info.append(f"User: {safe_preview(text, 200)}")
            if context_info:
                send_event("llm_thinking", {"message": " | ".join(context_info), "iteration": iteration})
            else:
                send_event("llm_thinking", {"message": f"Iteration {iteration}: Processing...", "iteration": iteration})
            
            logger.debug(f"[Tool Loop] Iteration {iteration}: Calling LLM with {len(input_items)} input_items")
            
            # Check if any message contains images - if so, use vision model
            # Use model explicitly set in agent, otherwise let provider decide
            current_model = (
                (active_skill_runtime.model_override if active_skill_runtime else None)
                or self.model
                or config.llm.get("model")
            )
            
            # Resolve provider: use config if set, otherwise use llm_client's default
            config_provider = config.llm.get("provider")
            if config_provider and isinstance(config_provider, str) and config_provider.strip():
                provider = config_provider.lower()
            else:
                provider = (getattr(llm_client, "default_provider", None) or "openai").lower()
            
            # Check if messages contain images (handle both top-level and nested in content)
            has_images = False
            for item in input_items:
                # Handle top-level image items
                if item.get("type") == "input_image":
                    has_images = True
                    break
                # Handle images nested inside a message's content list
                content = item.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "input_image":
                            has_images = True
                            break
                    if has_images:
                        break
            
            # Switch to vision model if current model doesn't support images
            effective_model = current_model
            if has_images:
                if current_model:
                    # Explicit model set but may not support vision
                    if not is_vision_model(provider, current_model):
                        fallback = get_vision_fallback_model(provider)
                        if fallback:
                            logger.info(f"[Tool Loop] Message contains images, switching from {current_model} to {fallback}")
                            effective_model = fallback
                else:
                    # No explicit model, use provider's vision default
                    fallback = get_vision_fallback_model(provider)
                    if fallback:
                        logger.info(f"[Tool Loop] Message contains images, using vision fallback {fallback}")
                        effective_model = fallback
            
            # Only pass model if explicitly set
            loop_tools = self.tools
            if active_skill_runtime and active_skill_runtime.tool_policy_declared:
                always_allowed = set(active_skill_runtime.allowed_tools_set)
                always_allowed.add("context_read_ref")
                loop_tools = intersect_tool_schemas_by_names(self.tools, always_allowed)

            llm_kwargs = dict(
                input_items=input_items,
                system_prompt=effective_system_prompt,
                tools=loop_tools,
                reasoning_replay=enable_reasoning,
            )
            latest_user_text = ""
            for _m in reversed(loop_messages):
                if isinstance(_m, dict) and _m.get("role") == "user":
                    latest_user_text = str(_m.get("content") or "")
                    break
            if _requires_source_complete_context(latest_user_text, active_skill_runtime, selected_skill, loop_messages):
                source_state = loop_context_state.setdefault("source", {}) if isinstance(loop_context_state, dict) else {}
                if not source_state.get("source_complete"):
                    source_target = _find_source_target_for_context(latest_user_text, loop_messages, loop_context_state)
                    jira_hint = source_target.get("target") if source_target.get("source_type") == "jira" else ""
                    confluence_hint = source_target.get("target") if source_target.get("source_type") == "confluence" else ""
                    source_manifest = ""
                    source_type = ""
                    try:
                        if jira_hint:
                            prepared = await _execute_tool_via_runtime_bus(
                                session_id=session_id,
                                tool_name="jira_prepare_issue_context",
                                args={"issue_key_or_url": jira_hint},
                            )
                            source_manifest = str(prepared.content or "")
                            source_type = "jira"
                        elif confluence_hint:
                            prepared = await _execute_tool_via_runtime_bus(
                                session_id=session_id,
                                tool_name="confluence_prepare_page_context",
                                args={"page_id_or_url": confluence_hint},
                            )
                            source_manifest = str(prepared.content or "")
                            source_type = "confluence"
                    except Exception:
                        source_manifest = ""
                    if source_manifest:
                        loop_messages.append({"role": "assistant", "content": f"[auto source context prepared]\n{source_manifest}"})
                        input_items = build_responses_input_items(loop_messages, session_id=session_id)
                        llm_kwargs["input_items"] = input_items
                        source_state["source_type"] = source_type
                        source_state["target"] = jira_hint or confluence_hint
                        source_state["source_complete"] = "source_complete: True" in source_manifest
                        comments_match = re.search(r"comments_loaded:\\s*([0-9]+)/([0-9]+)", source_manifest)
                        if comments_match:
                            source_state["comments_loaded"] = int(comments_match.group(1))
                            source_state["comments_total"] = int(comments_match.group(2))
                        attachments_match = re.search(r"attachments_(?:metadata_)?loaded:\\s*([0-9]+)/([0-9]+)", source_manifest)
                        if attachments_match:
                            source_state["attachments_loaded"] = int(attachments_match.group(1))
                            source_state["attachments_total"] = int(attachments_match.group(2))
                        source_state["source_bundle_ref_count"] = 1 if "context_ref:" in source_manifest else 0
                        source_state["source_digest_ref_count"] = 1 if "digest_ref:" in source_manifest else 0
                        chunk_match = re.search(r"source_digest_chunk_count:\\s*([0-9]+)", source_manifest)
                        source_state["source_digest_chunk_count"] = int(chunk_match.group(1)) if chunk_match else 0
                        source_state["source_partial_reasons_count"] = source_manifest.count("partial_reasons:")
            large_generation_guard = _large_generation_output_guard(
                active_skill_runtime=active_skill_runtime,
                selected_skill=selected_skill,
                active_skill_contract=active_skill_contract,
                latest_user_text=latest_user_text,
            )
            if large_generation_guard:
                llm_kwargs["system_prompt"] = ((llm_kwargs.get("system_prompt") or "") + "\n\n" + large_generation_guard).strip()
            large_generation_guard_applied = bool(large_generation_guard)
            if effective_model:
                llm_kwargs["model"] = effective_model
            
            # Pass provider to ensure correct LLM client routing
            if provider:
                llm_kwargs["provider"] = _normalize_provider_key(provider)

            request_estimated_tokens = estimate_llm_request_tokens(
                input_items=input_items,
                system_prompt=llm_kwargs.get("system_prompt", "") or "",
                tools=loop_tools or [],
            )
            loop_budget = resolve_prompt_budget(stage="tool_loop", model=effective_model)
            output_boundary = resolve_output_boundary(effective_model)
            model_max_tokens = int(loop_budget.get("max_output_tokens") or 64000)
            effective_max_tokens, max_token_diag = _resolve_effective_max_tokens(model_max_tokens)
            llm_kwargs["max_tokens"] = effective_max_tokens
            if isinstance(loop_context_state, dict):
                budget_state = loop_context_state.setdefault("budget", {})
                prompt_budget_tokens = int(loop_budget.get("prompt_budget_tokens", 0) or 0)
                budget_state["request_estimated_tokens"] = request_estimated_tokens
                budget_state["prompt_budget_tokens"] = prompt_budget_tokens
                budget_state["reserved_output_tokens"] = loop_budget.get("reserved_output_tokens")
                budget_state["safety_margin_tokens"] = loop_budget.get("safety_margin_tokens")
                budget_state["max_prompt_tokens"] = loop_budget.get("max_prompt_tokens")
                budget_state["request_over_budget"] = request_estimated_tokens > prompt_budget_tokens if prompt_budget_tokens else False
                budget_state["large_generation_guard_applied"] = large_generation_guard_applied
                budget_state["output_size_guard_applied"] = large_generation_guard_applied
                budget_state["large_generation_guard_reason"] = "classifier:skill_or_user_generation_request" if large_generation_guard_applied else ""
                budget_state["generation_mode"] = "staged" if large_generation_guard_applied else "default"
                budget_state["current_generation_phase"] = "manifest" if large_generation_guard_applied else ""
                budget_state["max_chat_output_tokens"] = output_boundary.get("max_chat_output_tokens")
                budget_state["max_chat_output_chars"] = output_boundary.get("max_chat_output_chars")
                budget_state["output_risk_level"] = "high" if large_generation_guard_applied else "normal"
                budget_state["output_token_limit"] = loop_budget.get("max_output_tokens")
                budget_state["configured_max_tokens"] = max_token_diag.get("configured_max_tokens")
                budget_state["effective_max_tokens"] = max_token_diag.get("effective_max_tokens")
                budget_state["legacy_max_tokens_ignored"] = bool(max_token_diag.get("legacy_max_tokens_ignored"))
                budget_state["input_context_usage_percent"] = (
                    (loop_context_state.get("budget") or {}).get("usage_percent")
                    if isinstance(loop_context_state.get("budget"), dict)
                    else None
                )
            if request_estimated_tokens > int(loop_budget.get("prompt_budget_tokens", 0) or 0):
                loop_messages, loop_context_state = await prepare_progressive_messages(
                    messages=loop_messages,
                    model=effective_model,
                    session_id=session_id,
                    stage="tool_loop_aggressive",
                    recent_count=3,
                )
                emit_context_snapshot("tool_loop_aggressive", loop_context_state, iteration=iteration)
                input_items = build_responses_input_items(loop_messages, session_id=session_id)
                llm_kwargs["input_items"] = input_items
                request_estimated_tokens = estimate_llm_request_tokens(
                    input_items=input_items,
                    system_prompt=llm_kwargs.get("system_prompt", "") or "",
                    tools=loop_tools or [],
                )
            if request_estimated_tokens > int(loop_budget.get("prompt_budget_tokens", 0) or 0):
                loop_messages = degrade_projected_context_sources(loop_messages)
                input_items = build_responses_input_items(loop_messages, session_id=session_id)
                llm_kwargs["input_items"] = input_items
                request_estimated_tokens = estimate_llm_request_tokens(
                    input_items=input_items,
                    system_prompt=llm_kwargs.get("system_prompt", "") or "",
                    tools=loop_tools or [],
                )
            if isinstance(loop_context_state, dict):
                budget_state = loop_context_state.setdefault("budget", {})
                prompt_budget_tokens = int(loop_budget.get("prompt_budget_tokens", 0) or 0)
                budget_state["request_estimated_tokens"] = request_estimated_tokens
                budget_state["prompt_budget_tokens"] = prompt_budget_tokens
                budget_state["reserved_output_tokens"] = loop_budget.get("reserved_output_tokens")
                budget_state["safety_margin_tokens"] = loop_budget.get("safety_margin_tokens")
                budget_state["max_prompt_tokens"] = loop_budget.get("max_prompt_tokens")
                budget_state["max_output_tokens"] = loop_budget.get("max_output_tokens")
                budget_state["max_chat_output_tokens"] = output_boundary.get("max_chat_output_tokens")
                budget_state["max_chat_output_chars"] = output_boundary.get("max_chat_output_chars")
                budget_state["configured_max_tokens"] = max_token_diag.get("configured_max_tokens")
                budget_state["effective_max_tokens"] = max_token_diag.get("effective_max_tokens")
                budget_state["legacy_max_tokens_ignored"] = bool(max_token_diag.get("legacy_max_tokens_ignored"))
                budget_state["request_over_budget"] = request_estimated_tokens > prompt_budget_tokens if prompt_budget_tokens else False
                budget_state["large_generation_guard_applied"] = large_generation_guard_applied
                budget_state["output_size_guard_applied"] = large_generation_guard_applied
                budget_state["request_budget_stage"] = "tool_loop"
                latest_request_budget = dict(budget_state)
                emit_context_snapshot("tool_loop_budget", loop_context_state, iteration=iteration)
            prompt_budget_tokens = int(loop_budget.get("prompt_budget_tokens", 0) or 0)
            if _should_abort_over_budget(request_estimated_tokens, prompt_budget_tokens):
                error_response = _build_context_budget_exceeded_error(
                    request_estimated_tokens=request_estimated_tokens,
                    loop_budget=loop_budget,
                    stage="tool_loop",
                )
                _merge_budget_into_error_details(error_response, latest_request_budget)
                return attach_runtime_events(error_response)
            if request_estimated_tokens > prompt_budget_tokens:
                llm_kwargs["system_prompt"] = (
                    (llm_kwargs.get("system_prompt") or "")
                    + "\n\nBudget guard: Do not emit all generated files in chat. "
                    "Write artifacts/files via tools when possible; otherwise output a concise manifest and ask to continue file-by-file."
                )
            
            raw_max_chat = (
                ((loop_context_state.get("budget", {}) or {}).get("max_chat_output_chars"))
                if isinstance(loop_context_state, dict)
                else None
            )
            llm_result, output_diag = await call_llm_with_output_control(
                llm_client=llm_client,
                llm_kwargs=llm_kwargs,
                session_id=session_id,
                stage="tool_loop",
                context_state=loop_context_state if isinstance(loop_context_state, dict) else {"budget": {}},
                active_skill=selected_skill,
                latest_user_text=latest_user_text,
                max_chat_output_chars=raw_max_chat,
            )
            if isinstance(loop_context_state, dict) and isinstance(loop_context_state.get("budget"), dict):
                loop_context_state["budget"]["output_controller_applied"] = True
                if isinstance(output_diag, dict):
                    loop_context_state["budget"]["output_risk_level"] = output_diag.get("output_risk_level")
                    loop_context_state["budget"]["generation_mode"] = output_diag.get("generation_mode") or loop_context_state["budget"].get("generation_mode")
                    if isinstance(output_diag.get("generation"), dict):
                        loop_context_state["generation"] = dict(output_diag.get("generation"))
            # Check for LLM configuration error
            if llm_result.get("error"):
                error_info = llm_result["error"]
                error_msg = error_info.get("message", "Unknown LLM error")
                logger.error(f"LLM error: {error_msg}")
                error_response = {
                    "error": error_msg,
                    "error_type": error_info.get("type", "llm_error"),
                    "code": error_info.get("code", "")
                }
                details = error_info.get("details")
                status_code = error_info.get("status_code")
                if isinstance(details, dict):
                    error_response["details"] = details
                if isinstance(status_code, int):
                    error_response["status_code"] = status_code
                _merge_budget_into_error_details(error_response, latest_request_budget)
                return attach_runtime_events(error_response)
            
            # Debug logging for LLM response
            if _is_debug_enabled():
                logger.debug(f"=== [AGENT] LLM RESPONSE (iter {iteration}) ===")
                content = llm_result.get('content') or ''
                logger.debug(f"Content length: {len(content)} chars")
                
                tool_calls = llm_result.get('tool_calls', [])
                logger.debug(f"Tool calls: {len(tool_calls)}")
                for tc in tool_calls:
                    tc_name = tc.get('function', {}).get('name', 'unknown')
                    logger.debug(f"  - {tc_name}")
            
            # Track usage
            if track_usage:
                iter_usage = llm_result.get("usage", {})
                if usage_data:
                    usage_data = {
                        "prompt_tokens": usage_data.get("prompt_tokens", 0) + iter_usage.get("prompt_tokens", 0),
                        "completion_tokens": usage_data.get("completion_tokens", 0) + iter_usage.get("completion_tokens", 0),
                        "total_tokens": usage_data.get("total_tokens", 0) + iter_usage.get("total_tokens", 0),
                    }
                else:
                    usage_data = iter_usage
            
            content = (llm_result.get("content") or "").strip()
            function_calls = llm_result.get("function_calls", [])
            tool_calls = function_calls  # alias
            staged_mode = bool(isinstance(loop_context_state, dict) and isinstance(loop_context_state.get("budget"), dict) and (loop_context_state.get("budget") or {}).get("generation_mode") == "staged")
            
            # Save intermediate chatlog after EVERY LLM call (for recovery on interruption)
            # Use asyncio.to_thread to avoid blocking the event loop
            async def save_chatlog():
                try:
                    from src.skills import get_tracer
                    tracer_instance = get_tracer()
                    all_events = tracer_instance.get_events_for_ui(limit=50, session_id=session_id)
                    
                    chatlog_data = {
                        "session_id": session_id,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "iteration": iteration,
                        "llm_debug": {
                            "llm_request": llm_result.get("_llm_debug", {}),
                            "thinking_events": all_events,
                        },
                        "thinking_events": all_events,
                    }
                    if active_skill_contract:
                        chatlog_data["skill_session"] = active_skill_contract
                    chatlog_dir = os.path.join(session_persistence.storage_dir, "chatlogs")
                    os.makedirs(chatlog_dir, exist_ok=True)
                    # Use raw session_id to match webchat.py's approach
                    chatlog_file = os.path.join(chatlog_dir, f"{session_id}.json")
                    # Atomic write: write to temp file first, then replace
                    import uuid
                    temp_chatlog_file = chatlog_file + f".{uuid.uuid4().hex[:8]}.tmp"
                    with open(temp_chatlog_file, "w") as f:
                        json.dump(chatlog_data, f, indent=2)
                    os.replace(temp_chatlog_file, chatlog_file)
                except Exception as e:
                    logger.debug(f"Failed to save intermediate chatlog: {e}")
            
            # Run chatlog save in background thread to avoid blocking
            asyncio.create_task(save_chatlog())

            # If no function calls, we're done - return the response
            if not tool_calls:
                # Fallback: if content is empty, try to use the latest tool result
                fallback_content = content
                if not content.strip():
                    # Find the latest tool result in loop_messages
                    for msg in reversed(loop_messages):
                        if msg.get("role") == "tool" and msg.get("content"):
                            fallback_content = str(msg.get("content"))
                            break
                    # If still empty, provide a default prompt
                    if not fallback_content.strip():
                        fallback_content = "Operation completed, but no detailed result was returned."
                assistant_extra = self._assistant_extra_from_payload(llm_result, fallback_content)
                await self._persist_assistant_message(
                    session_id,
                    fallback_content,
                    extra=assistant_extra,
                )
                result = self._build_assistant_result_payload(
                    fallback_content,
                    usage=usage_data,
                    user_message_id=user_message_id,
                    extra=assistant_extra,
                )
                if enable_reasoning:
                    reasoning_content = llm_result.get("reasoning", "")
                    result["reasoning"] = reasoning_content
                    # Send actual thinking content if reasoning is available
                    if reasoning_content:
                        send_event("llm_thinking", {
                            "message": safe_preview(reasoning_content, 500),  # Truncate for display
                            "thinking": reasoning_content,  # Full thinking for storage
                            "iteration": iteration
                        })
                        # Also log to tracer for persistence
                        try:
                            from src.skills import get_tracer
                            tracer_instance = get_tracer()
                            tracer_instance.log_thinking(reasoning_content)
                        except Exception:
                            pass
                else:
                    # No reasoning_replay: show context info instead
                    user_msg = ""
                    for item in input_items:
                        if item.get("type") == "message" and item.get("role") == "user":
                            user_msg = safe_preview(item.get("content", ""), 200)
                            break
                    if user_msg:
                        send_event("llm_thinking", {
                            "message": f"User: {user_msg}",
                            "context": "user_message",
                            "iteration": iteration
                        })
                # Send completion event
                send_event("complete", {
                    "response": truncate_with_count(fallback_content, 500),
                    "total_iterations": iteration
                })
                # Complete execution tracing
                tracer.complete_execution(fallback_content)
                # Get events for UI
                from src.skills import get_tracer
                tracer_instance = get_tracer()
                events = tracer_instance.get_events_for_ui(limit=10, session_id=session_id)
                result["events"] = events
                # Add complete thinking flow to debug info
                if llm_result and "_llm_debug" in llm_result:
                    # Get all events from tracer for complete flow
                    all_events = tracer_instance.get_events_for_ui(limit=50, session_id=session_id)
                    result["_llm_debug"] = {
                        "llm_request": llm_result["_llm_debug"],
                        "thinking_events": all_events,
                        "final_response": fallback_content,
                    }
                # Trigger memory update (async, fire and forget)
                # We need to get the last user message and assistant response
                recent_messages = await session_manager.get_history(session_id)
                user_text = ""
                assistant_text = fallback_content
                for msg in reversed(recent_messages):
                    if msg.get("role") == "user":
                        user_text = msg.get("content", "")
                        break

                # Disabled: Turn-based memory writing (only backfill at startup)
                # if user_text and self.memory_update_manager:
                #     try:
                #         await self.memory_update_manager.on_turn_completed(
                #             session_id=session_id,
                #             turn_id=sum(1 for m in recent_messages if m.get("role") == "user"),
                #             user_text=user_text,
                #             assistant_text=assistant_text,
                #         )
                #     except Exception as e:
                #         logger.debug(f"Memory update failed: {e}")
                
                return attach_runtime_events(result)
            
            logger.info(f"[Tool Loop] Iteration {iteration}: LLM requested {len(tool_calls)} tool calls")
            
            # Send actual thinking content if reasoning is available (for tool call iterations too)
            if enable_reasoning:
                reasoning_content = llm_result.get("reasoning", "")
                if reasoning_content:
                    send_event("llm_thinking", {
                        "message": reasoning_content[:500],
                        "thinking": reasoning_content,
                        "iteration": iteration
                    })
                    # Also log to tracer for persistence
                    try:
                        from src.skills import get_tracer
                        tracer_instance = get_tracer()
                        tracer_instance.log_thinking(reasoning_content)
                    except Exception:
                        pass
            
            # Check if LLM wants to call tools
            if not tool_calls:
                # No tool calls - return the response
                if enable_reasoning:
                    reasoning_content = llm_result.get("reasoning", "")
                    if reasoning_content:
                        send_event("llm_thinking", {
                            "message": reasoning_content[:500],
                            "thinking": reasoning_content,
                            "iteration": iteration
                        })
                
                # Build final result
                await self._persist_assistant_message(session_id, content)
                result = self._build_assistant_result_payload(
                    content,
                    usage=usage_data,
                    events=events,
                    user_message_id=user_message_id,
                    include_content_alias=True,
                    include_role=True,
                )
                
                # Add complete thinking flow to debug info
                if llm_result and "_llm_debug" in llm_result:
                    # Get all events from tracer for complete flow
                    all_events = tracer_instance.get_events_for_ui(limit=50, session_id=session_id)
                    result["_llm_debug"] = {
                        "llm_request": llm_result["_llm_debug"],
                        "thinking_events": all_events,
                        "final_response": content,
                    }
                
                return attach_runtime_events(result)
            
            # Record tool calls in loop_messages (ALL function calls, not just first);
            # input_items will be rebuilt from loop_messages on next iteration.
            # Convert Responses API format to Chat format for compaction compatibility.
            if tool_calls:
                chat_format_tool_calls = []
                for tc in tool_calls:
                    call_id = tc.get("call_id", "")
                    name = tc.get("name", "")
                    args = tc.get("arguments", {})
                    args_str = args if isinstance(args, str) else json.dumps(args)
                    chat_format_tool_calls.append({
                        "id": call_id,
                        "function": {
                            "name": name,
                            "arguments": args_str
                        }
                    })
                assistant_msg = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": chat_format_tool_calls,
                }
                loop_messages.append(assistant_msg)
                
                # NOTE: Do NOT save assistant message with tool_calls to session history here.
                # The final assistant response (without tool_calls) will be saved AFTER
                # tool execution completes. Saving tool_calls to history causes issues
                # because subsequent LLM calls see the tool_calls in history and return
                # new tool_calls, creating duplicates.
            
            # Note: Tool execution info is sent via WebSocket events and saved 
            # to session metadata via tracer (thinking_events). No message is saved
            # here - the final LLM response will be saved after tool execution.
            
            # Execute each function call
            executed_tool_results: List[tuple[str, ToolResult]] = []
            for fc in function_calls:
                call_id = fc.get("call_id", "")
                tool_name = fc.get("name", "")
                # Arguments can be dict or string - keep as string for API
                args_raw = fc.get("arguments", "{}")
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = args_raw or {}
                
                # Send tool call start event
                send_event("tool_call", {
                    "tool": tool_name,
                    "args": args,
                    "status": "executing"
                })

                # Runtime skill policy enforcement (hard guard, not prompt-only)
                if active_skill_runtime and active_skill_runtime.tool_policy_declared:
                    if not _is_tool_allowed_by_skill_runtime(tool_name, active_skill_runtime):
                        deny_result = build_skill_tool_denied_result(active_skill_runtime, tool_name)
                        logger.warning(
                            "[Skill] Runtime tool policy denied tool '%s' for skill '%s'",
                            tool_name,
                            active_skill_runtime.skill_name,
                        )
                        send_event(
                            "skill_tool_denied",
                            {
                                "skill": active_skill_runtime.skill_name,
                                "tool": tool_name,
                                "call_id": call_id,
                                "allowed_tools": active_skill_runtime.allowed_tools,
                                "internal_support_tools": sorted(INTERNAL_SUPPORT_TOOL_NAMES),
                            },
                        )
                        send_event(
                            "tool_result",
                            {
                                "tool": tool_name,
                                "call_id": call_id,
                                "result": safe_preview(deny_result, 500),
                                "success": False,
                            },
                        )
                        tracer.log_tool_call(
                            tool_name=tool_name,
                            arguments=redact_value(args),
                            result=safe_preview(deny_result, 500),
                            success=False,
                        )
                        loop_messages.append(
                            {
                                "role": "tool",
                                "content": _tool_feedback_text_for_tool(
                                    tool_name, deny_result, session_id=session_id, source_id=call_id or tool_name
                                ),
                                "tool_call_id": call_id,
                                "tool_name": tool_name,
                            }
                        )
                        executed_tool_results.append((tool_name, deny_result))
                        _run_post_tool_hooks_via_governance(
                            runtime_config=active_skill_runtime,
                            session_id=session_id,
                            tool_name=tool_name,
                            payload={"denied": True, "result": safe_preview(deny_result, 500)},
                            event_callback=send_event,
                        )
                        continue

                pre_hook_effects = _run_pre_tool_hooks_via_governance(
                    runtime_config=active_skill_runtime,
                    session_id=session_id,
                    tool_name=tool_name,
                    payload={"args": args},
                    event_callback=send_event,
                )
                if pre_hook_effects.modified_args:
                    args = {**args, **pre_hook_effects.modified_args}
                if pre_hook_effects.short_circuit_result is not None:
                    short_result = pre_hook_effects.short_circuit_result
                    short_result_preview = safe_preview(short_result, 200)
                    tracer.log_tool_call(
                        tool_name=tool_name,
                        arguments=redact_value(args),
                        result=safe_preview(short_result, 500),
                        success=short_result.success,
                    )
                    loop_messages.append(
                        {
                            "role": "tool",
                            "content": _tool_feedback_text_for_tool(
                                tool_name, short_result, session_id=session_id, source_id=call_id or tool_name
                            ),
                            "tool_call_id": call_id,
                            "tool_name": tool_name,
                        }
                    )
                    send_event("tool_result", {
                        "tool": tool_name,
                        "call_id": call_id,
                        "result": short_result_preview,
                        "success": short_result.success
                    })
                    executed_tool_results.append((tool_name, short_result))
                    _run_post_tool_hooks_via_governance(
                        runtime_config=active_skill_runtime,
                        session_id=session_id,
                        tool_name=tool_name,
                        payload={"short_circuit": True, "result": str(short_result)},
                        event_callback=send_event,
                    )
                    continue
                
                # ===== CONFIRMATION GATE (FR-4) =====
                # Check if this is a write operation that requires confirmation
                write_tools = {'github_comment_pr', 'github_add_comment', 'jira_add_comment', 
                              'git_commit', 'git_push', 'jira_transition'}
                
                if tool_name in write_tools:
                    logger.info(f"[Confirmation] Tool '{tool_name}' requires confirmation")
                    send_event("confirmation", {
                        "tool": tool_name,
                        "message": f"Write operation '{tool_name}' requires confirmation",
                        "auto_confirm": True
                    })
                    # For now, auto-confirm in default mode (can be made interactive later)
                    # TODO: Implement actual user confirmation flow
                    logger.info(f"[Confirmation] Auto-confirming write operation: {tool_name}")
                
                # Execute the tool through runtime bus (task-capable tools route to task boundary).
                logger.info(f"Executing tool: {tool_name} with args: {safe_preview(args, 300)}")
                tool_result = await _execute_tool_via_runtime_bus(
                    runtime_config=active_skill_runtime,
                    session_id=session_id,
                    tool_name=tool_name,
                    event_callback=send_event,
                    args=args,
                    source_ref="agents.core.tool_loop",
                )
                result_preview = safe_preview(tool_result, 200)
                post_hook_effects = _run_post_tool_hooks_via_governance(
                    runtime_config=active_skill_runtime,
                    session_id=session_id,
                    tool_name=tool_name,
                    payload={"success": tool_result.success, "result_preview": result_preview},
                    event_callback=send_event,
                )
                if post_hook_effects.result_override is not None:
                    tool_result = post_hook_effects.result_override
                    result_preview = safe_preview(tool_result, 200)
                tracer.log_tool_call(
                    tool_name=tool_name,
                    arguments=redact_value(args),
                    result=safe_preview(tool_result, 500),
                    success=tool_result.success,
                )
                # Send tool result event
                send_event("tool_result", {
                    "tool": tool_name,
                    "result": result_preview,
                    "success": tool_result.success
                })
                
                # Add tool result to loop_messages for the NEXT LLM call in the current request
                # IMPORTANT: Append to the END of loop_messages, not a specific position.
                # 
                # The issue with inserting at a specific position (i+1 after assistant with tool_calls)
                # is that loop_messages may contain old messages from conversation history.
                # Inserting at i+1 would place the tool result BEFORE those old user messages,
                # resulting in: assistant(tool_calls) -> tool_result -> user(history) [WRONG]
                #
                # By appending to the end, we get:
                #   ... old messages ... -> assistant(tool_calls) -> tool_result [CORRECT]
                # The tool result naturally comes after the assistant message in the iteration order.
                tool_result_msg = {
                    "role": "tool",
                    "content": _tool_feedback_text_for_tool(
                        tool_name, tool_result, session_id=session_id, source_id=call_id or tool_name
                    ),
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                }
                
                # Append tool result to end of loop_messages
                loop_messages.append(tool_result_msg)
                
                # NOTE: We do NOT save tool results to session history.
                # Tool results in session history cause ordering issues because:
                # 1. Assistant message with tool_calls is NOT saved (to prevent duplicate tool_calls)
                # 2. Tool result is saved separately
                # 3. When history is loaded, the order becomes wrong: user -> tool -> assistant
                # 
                # Instead, tool results stay in loop_messages for the current request's
                # execution context and are passed directly to subsequent LLM calls.
                
                logger.info(f"Tool result: {safe_preview(tool_result, 200)}")
                executed_tool_results.append((tool_name, tool_result))

            # Narrow passthrough shortcut for direct Jira detail retrieval requests.
            if len(executed_tool_results) == 1:
                _single_tool_name, single_tool_result = executed_tool_results[0]
                governance_hint = _read_governance_hint(single_tool_result)
                passthrough_recommended = bool(
                    isinstance(governance_hint, dict)
                    and governance_hint.get("tool_result_passthrough_recommended") is True
                )
                if passthrough_recommended:
                    passthrough_content = str(single_tool_result.content)
                    assistant_extra = self._assistant_extra_from_payload(
                        single_tool_result,
                        passthrough_content,
                    )
                    await self._persist_assistant_message(
                        session_id,
                        passthrough_content,
                        extra=assistant_extra,
                    )
                    send_event("complete", {
                        "response": truncate_with_count(passthrough_content, 500),
                        "total_iterations": iteration
                    })
                    tracer.complete_execution(passthrough_content)
                    from src.skills import get_tracer
                    tracer_instance = get_tracer()
                    events = tracer_instance.get_events_for_ui(limit=10, session_id=session_id)
                    result = self._build_assistant_result_payload(
                        passthrough_content,
                        usage=usage_data,
                        events=events,
                        user_message_id=user_message_id,
                        extra=assistant_extra,
                    )
                    return attach_runtime_events(result)
            
            # Send iteration complete event
            send_event("iteration_end", {"iteration": iteration})
            
            # Loop continues - LLM will decide next action based on tool results
            # This is the key: don't return after one tool call, let LLM decide
        
        # Safety: max iterations reached
        logger.warning(f"[Tool Loop] Max iterations ({max_tool_iterations}) reached")
        max_iterations_text = "Task completed after maximum iterations."
        await self._persist_assistant_message(session_id, max_iterations_text)
        
        # Send completion event
        send_event("complete", {
            "response": max_iterations_text,
            "total_iterations": max_tool_iterations,
            "note": "max_iterations"
        })
        
        tracer.complete_execution("max_iterations_reached")
        
        # Get events for UI
        from src.skills import get_tracer
        tracer_instance = get_tracer()
        events = tracer_instance.get_events_for_ui(limit=10, session_id=session_id)
        
        result = self._build_assistant_result_payload(
            max_iterations_text,
            usage=usage_data or {},
            events=events,
            user_message_id=user_message_id,
        )
        return attach_runtime_events(result)

    async def _start_skill_mode(
        self,
        message: str,
        session_id: str,
        user_message_id: str,
        skill: Any,
        track_usage: bool = True,
        stream_callback: Optional[Callable[[str], None]] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start a new lightweight skill-mode session."""
        from src.skills import get_tracer
        tracer = get_tracer()
        
        logger.debug(f"[SkillMode] ===== _start_skill_mode BEGIN =====")
        logger.debug(f"[SkillMode] message={safe_preview(message, 200)}")
        logger.debug(f"[SkillMode] session_id={session_id}, skill={skill.name if skill else None}")
        
        usage_data: Dict[str, Any] = {}

        def send_skill_event(event_type: str, data: dict):
            """Send skill event via stream_callback if available, and also emit to event_bus for WebSocket."""
            import json
            event_data = _enrich_runtime_event_context(
                data,
                session_id=session_id,
                agent_id=self.agent_id,
                request_id=request_id,
                task_id=data.get("task_id"),
                group_id=data.get("group_id") or data.get("portal_group_id"),
                coordination_run_id=data.get("coordination_run_id") or data.get("portal_coordination_run_id"),
            )
            event = json.dumps({"type": event_type, "data": event_data})

            # Send via stream_callback (for SSE)
            if stream_callback:
                try:
                    if hasattr(stream_callback, 'put'):
                        stream_callback.put_nowait(event)
                    else:
                        stream_callback(event)
                except Exception:
                    pass  # Ignore callback errors

            # Also emit to event_bus for WebSocket listeners
            try:
                from src.gateway.event_bus import event_bus
                event_bus.emit_sync(event_type, event_data)
            except Exception:
                pass  # Ignore if event_bus not available

        if skill.path:
            set_skill_workdir(skill.path)

        # Log skill mode entry
        tracer.log_skill_mode_entry(skill.name, message, session_id)
        send_skill_event("skill_mode_start", {"skill": skill.name, "message": safe_preview(message, 100)})

        # Generate initial plan (always returns 3-tuple: goal, steps, usage)
        tracer.log_skill_mode_step("GENERATE_PLAN", "started", f"Creating plan for: {safe_preview(message, 50)}")
        send_skill_event("skill_step", {"step": "GENERATE_PLAN", "status": "started", "detail": f"Creating plan..."})
        
        goal, steps, plan_usage = await generate_initial_skill_plan(skill, message, model=self.model)
        
        tracer.log_skill_mode_step("GENERATE_PLAN", "completed", f"Goal: {goal[:50]}...")
        send_skill_event("skill_step", {"step": "GENERATE_PLAN", "status": "completed", "detail": f"Goal: {goal[:100]}..."})

        skill_session = SkillSession(
            skill_name=skill.name,
            original_user_request=message,
            goal=goal,
            plan=steps,
            status="active",
        )

        await session_manager.set_active_skill_session(session_id, skill_session.to_dict())

        # Merge plan generation usage into usage_data
        if track_usage and plan_usage:
            usage_data = {
                "prompt_tokens": usage_data.get("prompt_tokens", 0) + plan_usage.get("prompt_tokens", 0),
                "completion_tokens": usage_data.get("completion_tokens", 0) + plan_usage.get("completion_tokens", 0),
                "total_tokens": usage_data.get("total_tokens", 0) + plan_usage.get("total_tokens", 0),
            }

        send_skill_event("skill_session_start", {"skill": skill.name, "goal": goal[:100]})

        return await self._continue_skill_mode(
            message=message,
            session_id=session_id,
            user_message_id=user_message_id,
            skill_state=skill_session.to_dict(),
            track_usage=track_usage,
            usage_data=usage_data,
            skill=skill,
            stream_callback=stream_callback,
            request_id=request_id,
        )

    async def _continue_skill_mode(
        self,
        message: str,
        session_id: str,
        user_message_id: str,
        skill_state: Dict[str, Any],
        track_usage: bool = True,
        usage_data: Optional[Dict[str, Any]] = None,
        skill: Any = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Continue an existing lightweight skill-mode session."""
        from src.skills import get_tracer
        tracer = get_tracer()
        
        logger.debug(f"[SkillMode] ===== _continue_skill_mode BEGIN =====")
        logger.debug(f"[SkillMode] message={safe_preview(message, 200)}")
        logger.debug(f"[SkillMode] session_id={session_id}, skill_state keys={list(skill_state.keys()) if skill_state else None}")
        
        usage_data = usage_data or {}

        def send_skill_event(event_type: str, data: dict):
            """Send skill event via stream_callback if available, and also emit to event_bus for WebSocket."""
            import json
            event_data = _enrich_runtime_event_context(
                data,
                session_id=session_id,
                agent_id=self.agent_id,
                request_id=request_id,
                task_id=data.get("task_id"),
                group_id=data.get("group_id") or data.get("portal_group_id"),
                coordination_run_id=data.get("coordination_run_id") or data.get("portal_coordination_run_id"),
            )
            event = json.dumps({"type": event_type, "data": event_data})
            logger.debug(f"[SkillMode] [EVENT] type={event_type}, data={safe_preview(event_data, 200)}")

            # Send via stream_callback (for SSE)
            if stream_callback:
                try:
                    if hasattr(stream_callback, 'put'):
                        stream_callback.put_nowait(event)
                    else:
                        stream_callback(event)
                except Exception:
                    pass  # Ignore callback errors

            # Also emit to event_bus for WebSocket listeners
            try:
                from src.gateway.event_bus import event_bus
                event_bus.emit_sync(event_type, event_data)
            except Exception:
                pass  # Ignore if event_bus not available

        from src.skills import skill_registry

        skill_session = SkillSession.from_dict(skill_state)
        skill = skill or skill_registry.get_skill(skill_session.skill_name)
        try:
            skill_runtime_config = (
                skill_registry.get_skill_runtime_config(
                    skill,
                    globally_allowed_tool_names=getattr(self, "allowed_tool_names", set()),
                )
                if skill else None
            )
        except Exception:
            logger.debug(
                "[SkillMode] Failed to resolve runtime config for skill %s; continuing without runtime config",
                getattr(skill, "name", None),
                exc_info=True,
            )
            skill_runtime_config = None
        if not skill:
            await session_manager.set_active_skill_session(session_id, None)
            fallback = "Skill session was cleared because the skill definition is unavailable."
            await self._persist_assistant_message(session_id, fallback)
            events = tracer.get_events_for_ui(limit=10, session_id=session_id)
            return self._build_assistant_result_payload(
                fallback,
                usage=usage_data,
                events=events,
                user_message_id=user_message_id,
            )

        logger.debug(f"[SkillMode] skill={skill.name}, skill_session.status={skill_session.status}")
        logger.debug(f"[SkillMode] skill_session.completed_steps count={len(skill_session.completed_steps)}")
        logger.debug(f"[SkillMode] skill_session.memory_summary length={len(skill_session.memory_summary) if skill_session.memory_summary else 0}")

        # Fresh user turn: reset progress-window fields while preserving accumulated session content
        skill_session.no_progress_count = 0
        skill_session.last_progress_signature = ""
        skill_session.last_tool_name = ""
        skill_session.last_tool_args_signature = ""
        skill_session.last_tool_output_signature = ""
        
        if skill.path:
            set_skill_workdir(skill.path)

        from src.agents.skill_mode import compact_skill_session_async, compact_skill_session_sync, estimate_tokens
        from src.agents.compaction import resolve_context_window_tokens
        
        provider = (config.llm.get("provider") or getattr(llm_client, "default_provider", "openai")).lower()
        max_skill_tool_rounds = 6  # Increased for longer skill execution
        max_skill_llm_calls = 10
        max_skill_compaction_budget = 32000  # 12% of 264K context for completed_steps
        
        # Resolve skill mode context window (similar to regular chat)
        model = self.model or config.llm.get("model", "gpt-5-mini")
        context_window = resolve_context_window_tokens(model)
        # Trigger compaction at 80% of context window
        skill_max_tokens = int(context_window * 0.8)
        
        raw_output = ""
        skill_request_budget: Dict[str, Any] = {}
        should_finalize_without_tools = False
        finalize_reason = ""
        skill_session.execution_mode = ""
        turn_state = SkillTurnState(
            llm_call_count=skill_session.llm_call_count,
            tool_round_count=skill_session.tool_round_count,
            lookup_only_hint=_is_lookup_only_skill(skill, skill_session, message),
        )

        user_prompt = _build_skill_mode_user_prompt(message, skill_session)
        input_items: List[Dict[str, Any]] = [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}]

        for round_num in range(max_skill_tool_rounds):
            logger.info(f"[SkillMode] ===== Round {round_num + 1}/{max_skill_tool_rounds} =====")
            turn_state.round_index = round_num
            turn_state.tool_round_count = skill_session.tool_round_count
            logger.info(
                "[SkillMode][Decision] round_start=%s execution_mode=%s no_progress_count=%s",
                round_num + 1,
                skill_session.execution_mode or "(unset)",
                skill_session.no_progress_count,
            )

            # Enforce max LLM call cap before making the next LLM request.
            if skill_session.llm_call_count >= max_skill_llm_calls:
                should_finalize_without_tools = True
                finalize_reason = "max_llm_calls"
                turn_state.transition = "max_llm_calls"
                break
            
            # Track tool activity within this round for counting semantics.
            round_tool_calls = []
            executed_any_tool_this_round = False
            
            # Estimate current token count from completed_steps and memory_summary
            current_steps_tokens = sum(
                estimate_tokens(step.get('result', '')) 
                for step in skill_session.completed_steps
            )
            memory_tokens = estimate_tokens(skill_session.memory_summary or '')
            current_tokens = current_steps_tokens + memory_tokens
            
            logger.debug(
                f"[SkillMode] Compaction check: "
                f"current_tokens={current_tokens}, max_tokens={skill_max_tokens}, "
                f"steps={len(skill_session.completed_steps)}, memory_chars={len(skill_session.memory_summary or '')}"
            )
            
            # Compact if over limit (same trigger as regular chat)
            if current_tokens > skill_max_tokens:
                logger.info(f"[SkillMode] Session exceeds token limit, compacting...")
                send_skill_event("skill_compaction", {"status": "started", "current_tokens": current_tokens, "max_tokens": skill_max_tokens})
                try:
                    skill_session = await compact_skill_session_async(skill_session, max_skill_compaction_budget)
                except Exception as compaction_err:
                    logger.warning(f"[SkillMode] Async compaction failed: {compaction_err}, using sync fallback")
                    skill_session = compact_skill_session_sync(skill_session, max_chars=4000)
                send_skill_event("skill_compaction", {"status": "completed", "remaining_steps": len(skill_session.completed_steps)})
            
            # Update token count after potential compaction
            if current_tokens > skill_max_tokens:
                current_steps_tokens = sum(
                    estimate_tokens(step.get('result', '')) 
                    for step in skill_session.completed_steps
                )
                memory_tokens = estimate_tokens(skill_session.memory_summary or '')
                current_tokens = current_steps_tokens + memory_tokens
                logger.info(
                    f"[SkillMode] After compaction: "
                    f"current_tokens={current_tokens}, steps_count={len(skill_session.completed_steps)}"
                )
            
            # Build prompts with potentially compacted session
            system_prompt = _build_skill_mode_system_prompt(skill, skill_session)
            
            logger.debug(f"[SkillMode] system_prompt length={len(system_prompt)}")
            logger.debug(f"[SkillMode] input_items count={len(input_items)}")
            
            # Get tools - always from globally filtered tools surface, then intersect with skill tools if present
            skill_tool_names = getattr(skill, 'tools', []) or []
            globally_allowed_tool_names = getattr(self, "allowed_tool_names", None)
            if not globally_allowed_tool_names:
                globally_allowed_tool_names = {
                    name
                    for name in (extract_tool_name(schema) for schema in (self.tools or []))
                    if name
                }
            
            # If skill defines tool names (List[str]), intersect from globally filtered tools only.
            # If skill defines tool schemas (List[Dict]), intersect with globally allowed names.
            if skill_tool_names and isinstance(skill_tool_names[0], str):
                available_tools = intersect_tool_schemas_by_names(self.tools, set(skill_tool_names or []) | {"context_read_ref"})
                logger.debug(
                    "[SkillMode] Intersected skill tool names with llm.tools policy: requested=%s available=%s",
                    len(skill_tool_names),
                    len(available_tools),
                )
            elif not skill_tool_names and self.tools:
                available_tools = self.tools
                logger.debug("[SkillMode] No skill-specific tools; using globally filtered tools")
            else:
                skill_tool_schemas = skill_tool_names if skill_tool_names and isinstance(skill_tool_names[0], dict) else []
                available_tools = [
                    schema for schema in skill_tool_schemas
                    if (extract_tool_name(schema) or "") in globally_allowed_tool_names
                ]
                support_tool_schemas = [
                    schema
                    for schema in (self.tools or [])
                    if (extract_tool_name(schema) or "").lower() in {name.lower() for name in INTERNAL_SUPPORT_TOOL_NAMES}
                ]
                existing_tool_names = {(extract_tool_name(schema) or "").lower() for schema in available_tools}
                for schema in support_tool_schemas:
                    support_name = (extract_tool_name(schema) or "").lower()
                    if support_name and support_name not in existing_tool_names:
                        available_tools.append(schema)
                        existing_tool_names.add(support_name)
                logger.debug(
                    "[SkillMode] Intersected skill tool schemas with llm.tools policy: declared=%s available=%s",
                    len(skill_tool_schemas),
                    len(available_tools),
                )
            
            logger.debug(f"[SkillMode] available_tools count={len(available_tools)}")
            if available_tools and isinstance(available_tools[0], dict):
                logger.debug(f"[SkillMode] tools: {[t.get('function', {}).get('name') or t.get('name') for t in available_tools]}")
            
            llm_kwargs = {
                "input_items": input_items,
                "system_prompt": system_prompt,
                "tools": available_tools,  # Use skill's tools or fall back to all tools
                "reasoning_replay": False,
                "provider": _normalize_provider_key(provider),
            }
            latest_user_text = ""
            latest_user_text = str(message or "")
            if _requires_source_complete_context(latest_user_text, skill_runtime_config, skill, []):
                source_target = _find_source_target_for_context(
                    latest_user_text,
                    messages=[],
                    context_state={"source": (skill_state.get("source") if isinstance(skill_state, dict) else {})},
                )
                jira_hint = source_target.get("target") if source_target.get("source_type") == "jira" else ""
                confluence_hint = source_target.get("target") if source_target.get("source_type") == "confluence" else ""
                source_manifest = ""
                if jira_hint:
                    prepared = await _execute_tool_via_runtime_bus(
                        session_id=session_id,
                        tool_name="jira_prepare_issue_context",
                        args={"issue_key_or_url": jira_hint},
                    )
                    source_manifest = str(prepared.content or "")
                elif confluence_hint:
                    prepared = await _execute_tool_via_runtime_bus(
                        session_id=session_id,
                        tool_name="confluence_prepare_page_context",
                        args={"page_id_or_url": confluence_hint},
                    )
                    source_manifest = str(prepared.content or "")
                if source_manifest:
                    input_items.append({"role": "assistant", "content": f"[auto source context prepared]\n{source_manifest}"})
                    llm_kwargs["input_items"] = input_items
            large_generation_guard = _large_generation_output_guard(
                active_skill_runtime=skill_runtime_config,
                selected_skill=skill,
                active_skill_contract=skill_state if isinstance(skill_state, dict) else None,
                latest_user_text=latest_user_text,
            )
            if large_generation_guard:
                llm_kwargs["system_prompt"] = ((llm_kwargs.get("system_prompt") or "") + "\n\n" + large_generation_guard).strip()
            large_generation_guard_applied = bool(large_generation_guard)
            if self.model:
                llm_kwargs["model"] = self.model

            request_estimated_tokens = estimate_llm_request_tokens(
                input_items=input_items,
                system_prompt=llm_kwargs.get("system_prompt", "") or "",
                tools=available_tools or [],
            )
            loop_budget = resolve_prompt_budget(stage="skill_generation", model=llm_kwargs.get("model"))
            output_boundary = resolve_output_boundary(llm_kwargs.get("model"))
            model_max_tokens = int(loop_budget.get("max_output_tokens") or 64000)
            effective_max_tokens, max_token_diag = _resolve_effective_max_tokens(model_max_tokens)
            llm_kwargs["max_tokens"] = effective_max_tokens
            prompt_budget_tokens = int(loop_budget.get("prompt_budget_tokens", 0) or 0)
            skill_request_budget = {
                "request_estimated_tokens": request_estimated_tokens,
                "prompt_budget_tokens": prompt_budget_tokens,
                "reserved_output_tokens": loop_budget.get("reserved_output_tokens"),
                "safety_margin_tokens": loop_budget.get("safety_margin_tokens"),
                "max_prompt_tokens": loop_budget.get("max_prompt_tokens"),
                "max_output_tokens": loop_budget.get("max_output_tokens"),
                "configured_max_tokens": max_token_diag.get("configured_max_tokens"),
                "effective_max_tokens": max_token_diag.get("effective_max_tokens"),
                "legacy_max_tokens_ignored": bool(max_token_diag.get("legacy_max_tokens_ignored")),
                "request_over_budget": request_estimated_tokens > prompt_budget_tokens if prompt_budget_tokens else False,
                "large_generation_guard_applied": large_generation_guard_applied,
                "output_size_guard_applied": large_generation_guard_applied,
                "large_generation_guard_reason": "classifier:skill_or_user_generation_request" if large_generation_guard_applied else "",
                "generation_mode": "staged" if large_generation_guard_applied else "default",
                "current_generation_phase": "manifest" if large_generation_guard_applied else "",
                "max_chat_output_tokens": output_boundary.get("max_chat_output_tokens"),
                "max_chat_output_chars": output_boundary.get("max_chat_output_chars"),
                "request_budget_stage": "skill_generation",
                "stage": "skill_generation",
            }
            if request_estimated_tokens > prompt_budget_tokens:
                skill_session = compact_skill_session_sync(skill_session, max_chars=4000)
                system_prompt = _build_skill_mode_system_prompt(skill, skill_session)
                input_items = degrade_projected_context_sources_in_responses_input_items(input_items)
                llm_kwargs["system_prompt"] = system_prompt
                if large_generation_guard:
                    llm_kwargs["system_prompt"] = (
                        (llm_kwargs.get("system_prompt") or "") + "\n\n" + large_generation_guard
                    ).strip()
                llm_kwargs["input_items"] = input_items
                request_estimated_tokens = estimate_llm_request_tokens(
                    input_items=input_items,
                    system_prompt=llm_kwargs.get("system_prompt", "") or "",
                    tools=available_tools or [],
                )
                skill_request_budget.update(
                    {
                        "request_estimated_tokens": request_estimated_tokens,
                        "request_over_budget": request_estimated_tokens > prompt_budget_tokens if prompt_budget_tokens else False,
                    }
                )
            if _should_abort_over_budget(request_estimated_tokens, prompt_budget_tokens):
                error_response = {
                    "error": "LLM request remains over prompt budget after skill-mode context projection.",
                    "error_type": "context_budget_exceeded",
                    "code": "context_budget_exceeded",
                    "details": {
                        "request_estimated_tokens": request_estimated_tokens,
                        "prompt_budget_tokens": prompt_budget_tokens,
                        "reserved_output_tokens": loop_budget.get("reserved_output_tokens"),
                        "safety_margin_tokens": loop_budget.get("safety_margin_tokens"),
                        "max_prompt_tokens": loop_budget.get("max_prompt_tokens"),
                        "max_output_tokens": loop_budget.get("max_output_tokens"),
                        "request_over_budget": True,
                        "request_budget_stage": "skill_generation",
                        "stage": "skill_generation",
                        "suggestion": "Split generation into smaller steps or write artifacts/files instead of emitting full content in chat.",
                    },
                    "user_message_id": user_message_id,
                    "request_budget": dict(skill_request_budget),
                }
                return error_response

            logger.debug(f"[SkillMode] Calling LLM with model={llm_kwargs.get('model')}, max_tokens={llm_kwargs.get('max_tokens')}")
            llm_result, output_diag = await call_llm_with_output_control(
                llm_client=llm_client,
                llm_kwargs=llm_kwargs,
                session_id=session_id,
                stage="skill_generation",
                context_state={"budget": skill_request_budget},
                active_skill=skill,
                latest_user_text=latest_user_text,
                max_chat_output_chars=None,
            )
            skill_session.llm_call_count += 1
            turn_state.llm_call_count = skill_session.llm_call_count
            logger.debug(f"[SkillMode] LLM response keys={llm_result.keys() if llm_result else None}")
            logger.debug(f"[SkillMode] LLM response content length={len(llm_result.get('content', '') or '')}")

            if isinstance(output_diag, dict):
                skill_request_budget["output_controller_applied"] = True
                skill_request_budget["output_risk_level"] = output_diag.get("output_risk_level")
                skill_request_budget["generation_mode"] = output_diag.get("generation_mode") or skill_request_budget.get("generation_mode")
                if isinstance(output_diag.get("generation"), dict):
                    skill_state["generation"] = dict(output_diag.get("generation"))
            if llm_result.get("error"):
                error_info = llm_result["error"]
                error_msg = error_info.get("message", "Unknown LLM error")
                logger.error(f"[SkillMode] LLM error: {error_msg}")
                error_response = {
                    "error": error_msg,
                    "error_type": error_info.get("type", "llm_error"),
                    "code": error_info.get("code", ""),
                    "user_message_id": user_message_id,
                }
                details = error_info.get("details")
                status_code = error_info.get("status_code")
                if isinstance(details, dict):
                    error_response["details"] = details
                if isinstance(status_code, int):
                    error_response["status_code"] = status_code
                _merge_budget_into_error_details(error_response, skill_request_budget)
                return error_response

            if track_usage:
                iter_usage = llm_result.get("usage", {}) or {}
                usage_data = {
                    "prompt_tokens": usage_data.get("prompt_tokens", 0) + iter_usage.get("prompt_tokens", 0),
                    "completion_tokens": usage_data.get("completion_tokens", 0) + iter_usage.get("completion_tokens", 0),
                    "total_tokens": usage_data.get("total_tokens", 0) + iter_usage.get("total_tokens", 0),
                }

            raw_output = (llm_result.get("content") or "").strip()
            function_calls = llm_result.get("function_calls", []) or llm_result.get("tool_calls", []) or []
            turn_state.has_function_calls = bool(function_calls)

            logger.debug(f"[SkillMode] raw_output={safe_preview(raw_output, 300)}")
            logger.debug(f"[SkillMode] function_calls count={len(function_calls)}")
            
            # Log full response if content is empty for debugging
            if not raw_output and not function_calls:
                logger.warning(f"[SkillMode] WARNING: LLM returned empty content AND no function_calls!")
                logger.warning(f"[SkillMode] Full llm_result keys: {llm_result.keys() if llm_result else None}")
                logger.warning(f"[SkillMode] llm_result: {safe_preview(llm_result, 500)}")

            if not function_calls:
                should_finalize_without_tools = True
                if turn_state.has_readonly_success and not turn_state.has_write_call and turn_state.lookup_only_hint:
                    finalize_reason = "lookup_complete"
                else:
                    finalize_reason = "no_function_calls"
                turn_state.transition = "finalizing"
                break

            # Keep assistant text context if model returned text together with tool calls
            if raw_output:
                should_project_output = (
                    len(raw_output) > 4000
                    or "```" in raw_output
                    or re.search(r"\b(Feature:|Scenario:|Scenario Outline:|class\s+\w+|def\s+\w+)\b", raw_output) is not None
                )
                projected_output = (
                    project_skill_assistant_content_for_input_items(
                        raw_output,
                        session_id=session_id,
                        round_num=round_num + 1,
                    )
                    if should_project_output
                    else raw_output
                )
                input_items.append({"role": "assistant", "content": projected_output})

            for call in function_calls:
                call_id = call.get("id") or call.get("call_id") or f"skill_call_{len(input_items)}"
                function_payload = call.get("function", {}) or {}
                tool_name = function_payload.get("name") or call.get("name", "")
                args_raw = function_payload.get("arguments", {}) or call.get("arguments", {})
                args_str = args_raw if isinstance(args_raw, str) else json.dumps(args_raw, ensure_ascii=False)

                logger.debug(f"[SkillMode] [TOOL_CALL] tool={tool_name}, args={safe_preview(args_str, 200)}")

                input_items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": tool_name,
                    "arguments": args_str,
                })
                
                round_tool_calls.append((tool_name, safe_preview(args_str, 100)))

                normalized_args = _normalize_tool_args(args_str)
                if skill_runtime_config and skill_runtime_config.tool_policy_declared:
                    if not _is_tool_allowed_by_skill_runtime(tool_name, skill_runtime_config):
                        deny_result = build_skill_tool_denied_result(skill_runtime_config, tool_name)
                        output_text = _tool_feedback_text_for_tool(
                            tool_name,
                            deny_result,
                            session_id=session_id,
                            source_id=call_id or tool_name,
                        )
                        input_items.append({
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output_text,
                        })
                        tracer.log_tool_call(
                            tool_name,
                            redact_value(normalized_args),
                            safe_preview(output_text, 500),
                            success=False,
                            error="denied_by_skill_policy",
                        )
                        send_skill_event(
                            "skill_tool_denied",
                            {
                                "tool": tool_name,
                                "call_id": call_id,
                                "allowed_tools": skill_runtime_config.allowed_tools,
                                "internal_support_tools": sorted(INTERNAL_SUPPORT_TOOL_NAMES),
                                "status": "denied",
                            },
                        )
                        round_tool_calls.append((tool_name, "denied_by_skill_policy"))
                        logger.warning(
                            "[SkillMode] Runtime skill policy denied tool '%s' for skill '%s'",
                            tool_name,
                            skill_runtime_config.skill_name,
                        )
                        continue
                # ===== CONFIRMATION GATE (skill-mode) =====
                # Check if this is a write operation that requires confirmation
                write_tools = {'github_comment_pr', 'github_add_comment', 'jira_add_comment', 
                              'git_commit', 'git_push', 'jira_transition'}
                
                if tool_name in write_tools:
                    turn_state.has_write_call = True
                    logger.info(f"[Confirmation][SkillMode] Tool '{tool_name}' requires confirmation, auto-confirming")
                
                logger.debug(f"[SkillMode] [TOOL_EXEC] Executing tool={tool_name}, parsed_args={safe_preview(normalized_args, 200)}")
                executed_any_tool_this_round = True
                try:
                    tool_result: ToolResult = await _execute_tool_via_runtime_bus(
                        runtime_config=skill_runtime_config,
                        session_id=session_id,
                        tool_name=tool_name,
                        args=normalized_args,
                        source_ref="agents.core.skill_mode_loop",
                    )
                    output_text = _tool_feedback_text_for_tool(
                        tool_name, tool_result, session_id=session_id, source_id=call_id or tool_name
                    )
                    logger.debug(f"[SkillMode] [TOOL_RESULT] tool={tool_name}, result length={len(output_text)}, preview={safe_preview(output_text, 200)}")
                except Exception as tool_exc:
                    output_text = f"Error: Tool '{tool_name}' failed with {tool_exc}"
                    logger.error(f"[SkillMode] [TOOL_ERROR] tool={tool_name}, error={sanitize_exception_message(tool_exc)}")

                input_items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_text,
                })
                
                logger.info(f"[SkillMode] [TOOL_FEEDBACK] Added to input_items, output length={len(output_text)}, preview={safe_preview(output_text, 300)}")
                
                # Log tool call for skill mode
                tracer.log_tool_call(tool_name, redact_value(normalized_args), safe_preview(output_text, 500))
                
                if not output_text.startswith("Error:"):
                    readonly_markers = ("get", "list", "query", "search", "fetch", "read", "issue", "pr", "file")
                    write_markers = ("comment", "create", "update", "push", "transition", "commit", "delete", "write")
                    lower_tool_name = tool_name.lower()
                    is_write_tool = any(marker in lower_tool_name for marker in write_markers)
                    if any(marker in lower_tool_name for marker in readonly_markers) and not is_write_tool:
                        turn_state.has_readonly_success = True
                        skill_session.execution_mode = "readonly_lookup"
                    elif is_write_tool:
                        skill_session.execution_mode = "producing_output"
                    skill_session.completed_steps.append(
                        {
                            "type": "tool_result",
                            "tool": tool_name,
                            "result": output_text[:1200],
                        }
                    )
                    skill_session.memory_summary = _update_skill_memory_summary(
                        skill_session, message, f"{tool_name}: {output_text[:800]}"
                    )
                    progress_eval = _evaluate_skill_progress(
                        skill_session=skill_session,
                        tool_name=tool_name,
                        normalized_args=normalized_args,
                        output_text=output_text,
                    )
                    if not progress_eval["progressed"]:
                        skill_session.no_progress_count += 1
                        logger.info(
                            "[SkillMode][Progress] unchanged tool=%s reason=%s no_progress_count=%s",
                            tool_name,
                            progress_eval["reason"],
                            skill_session.no_progress_count,
                        )
                    else:
                        skill_session.no_progress_count = 0
                        skill_session.last_progress_signature = progress_eval["state_signature"]
                        logger.info("[SkillMode][Progress] changed tool=%s", tool_name)
                    skill_session.last_tool_name = tool_name
                    skill_session.last_tool_args_signature = progress_eval["args_signature"]
                    skill_session.last_tool_output_signature = progress_eval["output_signature"]
                    await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
                
                # Stream tool call event for real-time UI updates
                send_skill_event("tool_call", {
                    "tool": tool_name,
                    "args": args_str[:500] if args_str else "",
                    "result": output_text[:500] if output_text else "",
                    "status": "completed" if not output_text.startswith("Error:") else "error"
                })

            # Log tools called in this round
            logger.info(f"[SkillMode] Round {round_num + 1} tools called: {round_tool_calls}")
            if executed_any_tool_this_round:
                skill_session.tool_round_count += 1
                turn_state.tool_round_count = skill_session.tool_round_count
            
            if should_finalize_without_tools:
                break

            if skill_session.no_progress_count >= 2:
                should_finalize_without_tools = True
                finalize_reason = "no_progress"
                turn_state.transition = "no_progress"
                break

            if round_num >= max_skill_tool_rounds - 1:
                should_finalize_without_tools = True
                finalize_reason = "max_tool_rounds"
                turn_state.transition = "max_tool_rounds"
                break

        if should_finalize_without_tools:
            turn_state.transition = "finalizing" if turn_state.transition == "tool_followup" else turn_state.transition
            skill_session.finalizer_attempts = 0
            skill_session.finalizer_state = "idle"
            remaining_llm_budget = max(0, max_skill_llm_calls - skill_session.llm_call_count)
            finalizer_result, usage_data = await _run_skill_finalizer(
                input_items=input_items,
                system_prompt=system_prompt,
                provider=provider,
                model=self.model,
                skill_session=skill_session,
                track_usage=track_usage,
                usage_data=usage_data,
                remaining_llm_budget=remaining_llm_budget,
                max_tokens=int(skill_request_budget.get("max_output_tokens") or config.llm.get("max_tokens", 64000) or 64000),
            )
            if isinstance(finalizer_result.request_budget, dict) and finalizer_result.request_budget:
                skill_request_budget.update(finalizer_result.request_budget)
            raw_output = finalizer_result.raw_output
            if finalizer_result.state == "terminal_failed":
                turn_state.transition = "terminal_failed"

        if not raw_output:
            # No function calls and no text output after max rounds
            # Check if we accomplished something via tool calls
            completed = skill_session.completed_steps if skill_session else []
            logger.info(f"[SkillMode] Loop ended with {len(completed)} completed steps")
            if completed:
                logger.info(f"[SkillMode] completed_steps details: {completed}")
                # Generate a summary from completed steps
                summaries = []
                for step in completed[-5:]:  # Last 5 steps
                    if step.get("type") == "execute" and step.get("result"):
                        summaries.append(step["result"][:200])  # Truncate each
                if summaries:
                    raw_output = f"[FINISH] Successfully completed {len(completed)} steps:\n" + "\n".join(f"- {s}" for s in summaries)
                    logger.info(f"[SkillMode] Auto-generated finish from {len(completed)} completed steps")
                else:
                    raw_output = "I could not produce a final skill-mode response. The model did not return any output or valid response."
            else:
                raw_output = "I could not produce a final skill-mode response. The model did not return any output or valid response."

        action, body = _parse_skill_control_marker(raw_output)
        if should_finalize_without_tools and not skill_session.termination_reason:
            if skill_session.finalizer_state == "succeeded":
                skill_session.termination_reason = finalize_reason or "finalizer_succeeded"
            elif skill_session.finalizer_state == "terminal_failed":
                skill_session.termination_reason = finalize_reason or "finalizer_terminal_failed"
            elif finalize_reason:
                skill_session.termination_reason = finalize_reason
            skill_session.transition = turn_state.transition
            await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
        
        # Log skill mode action with raw output for debugging
        logger.info(f"[SkillMode] Parsed action={action}, body_preview={safe_preview(body or '', 100)}, raw_output_preview={safe_preview(raw_output or '', 100)}")
        tracer.log_skill_mode_action(action, body)

        if action == "ask_user":
            question = body.strip() or "Please provide the minimum necessary information to continue."
            skill_session.status = "waiting_user"
            skill_session.execution_mode = "waiting_user"
            skill_session.termination_reason = "ask_user"
            skill_session.transition = "ask_user"
            skill_session.pending_question = question
            skill_session.memory_summary = _update_skill_memory_summary(skill_session, message, question)
            tracer.log_skill_mode_step("ASK_USER", "completed", f"Question: {question[:50]}...")
            send_skill_event("skill_step", {"step": "ASK_USER", "status": "completed", "detail": safe_preview(question, 200)})
            await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
            await self._persist_assistant_message(session_id, question)
            # Get events for UI
            events = tracer.get_events_for_ui(limit=10, session_id=session_id)
            send_skill_event("skill_complete", {"reason": "ask_user", "question": safe_preview(question, 200)})
            logger.info("[SkillMode][Terminate] reason=%s", skill_session.termination_reason)
            logger.debug(f"[SkillMode] ===== _continue_skill_mode END (ASK_USER) =====")
            return self._build_assistant_result_payload(
                question,
                usage=usage_data,
                events=events,
                user_message_id=user_message_id,
                request_budget=dict(skill_request_budget) if skill_request_budget else {},
            )

        if action == "finish":
            final_text = body.strip() or "Skill task completed."
            skill_session.status = "finished"
            skill_session.execution_mode = "producing_output"
            if skill_session.finalizer_state == "succeeded":
                skill_session.termination_reason = finalize_reason or "finalizer_succeeded"
            elif skill_session.finalizer_state == "terminal_failed":
                skill_session.termination_reason = finalize_reason or "finalizer_terminal_failed"
            elif finalize_reason in {"lookup_complete", "no_function_calls", "no_progress", "max_tool_rounds", "max_llm_calls"}:
                skill_session.termination_reason = finalize_reason
            else:
                skill_session.termination_reason = "finish"
            skill_session.transition = skill_session.transition or "finish"
            skill_session.completed_steps.append(
                {
                    "type": "terminal_snapshot",
                    "transition": skill_session.transition,
                    "termination_reason": skill_session.termination_reason,
                    "finalizer_state": skill_session.finalizer_state,
                    "finalizer_attempts": skill_session.finalizer_attempts,
                    "tool_round_count": skill_session.tool_round_count,
                    "llm_call_count": skill_session.llm_call_count,
                    "execution_mode": skill_session.execution_mode,
                }
            )
            skill_session.completed_steps.append({"type": "finish", "result": final_text})
            terminal_snapshot = {
                "status": skill_session.status,
                "transition": skill_session.transition,
                "termination_reason": skill_session.termination_reason,
                "finalizer_state": skill_session.finalizer_state,
                "finalizer_attempts": skill_session.finalizer_attempts,
                "tool_round_count": skill_session.tool_round_count,
                "llm_call_count": skill_session.llm_call_count,
                "execution_mode": skill_session.execution_mode,
            }
            tracer.log_skill_mode_step("FINISH", "completed", f"Result: {final_text[:50]}...")
            tracer.log_skill_mode_complete(final_text)
            send_skill_event("skill_step", {"step": "FINISH", "status": "completed", "detail": safe_preview(final_text, 200)})
            send_skill_event("skill_complete", {"reason": "finish", "result": safe_preview(final_text, 200)})
            await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
            await session_manager.set_active_skill_session(session_id, None)
            finish_extra = {"terminal_skill_session": terminal_snapshot}
            await self._persist_assistant_message(
                session_id,
                final_text,
                extra=finish_extra,
            )
            # Get events for UI
            events = tracer.get_events_for_ui(limit=10, session_id=session_id)
            logger.info("[SkillMode][Terminate] reason=%s", skill_session.termination_reason)
            logger.debug(f"[SkillMode] ===== _continue_skill_mode END (FINISH) =====")
            return self._build_assistant_result_payload(
                final_text,
                usage=usage_data,
                events=events,
                user_message_id=user_message_id,
                request_budget=dict(skill_request_budget) if skill_request_budget else {},
            )

        # default: execute
        was_waiting_user = skill_session.status == "waiting_user"
        result_text = body.strip() if body.strip() else raw_output
        tracer.log_skill_mode_step("EXECUTE", "completed", f"Result preview: {result_text[:50]}...")
        send_skill_event("skill_step", {"step": "EXECUTE", "status": "completed", "detail": safe_preview(result_text, 200)})
        if len(result_text) < 30:
            result_text = f"{result_text}\n\n(Continuing skill execution, will ask if more info is needed.)"

        skill_session.status = "active"
        skill_session.execution_mode = "producing_output"
        skill_session.termination_reason = ""
        skill_session.transition = "tool_followup"
        skill_session.pending_question = None
        skill_session.completed_steps.append(
            {
                "type": "execute",
                "result": result_text,
            }
        )
        try:
            turn_artifacts = _extract_skill_artifacts(
                skill_session=skill_session,
                user_message=message,
                latest_result=result_text,
                was_waiting_user=was_waiting_user,
            )
            skill_session.artifacts = _merge_skill_artifacts(skill_session.artifacts, turn_artifacts)
        except Exception as artifact_exc:
            logger.warning(f"[SkillMode] Artifact extraction failed: {artifact_exc}")
        skill_session.memory_summary = _update_skill_memory_summary(skill_session, message, result_text)

        await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
        await self._persist_assistant_message(session_id, result_text)
        # Get events for UI
        events = tracer.get_events_for_ui(limit=10, session_id=session_id)
        return self._build_assistant_result_payload(
            result_text,
            usage=usage_data,
            events=events,
            user_message_id=user_message_id,
            request_budget=dict(skill_request_budget) if skill_request_budget else {},
        )

    async def _execute_skill(
        self,
        skill_name: str,
        message: str,
        session_id: str,
    ) -> str:
        """Execute a skill and return the result."""
        try:
            # Parse command and arguments from message
            # Support formats:
            # - "git pull repo_path=/path" (natural language)
            # - "git command=pull repo_path=/path" (explicit command)
            parts = message.split()
            if not parts:
                return "Error: Empty message"
            
            # Extract sub-command and arguments
            sub_command = None
            args = {}
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    value = value.strip("'\"")
                    if value.isdigit():
                        value = int(value)
                    args[key] = value
                elif sub_command is None:
                    # First non-key=value part is the command
                    sub_command = part.lower()
            
            # Default command if not found
            if sub_command is None:
                sub_command = "status"
            
            result = await skills_executor.execute_skill(
                skill_name,
                command=sub_command,
                message=message,
                **args
            )

            if result.success:
                if result.data:
                    return f"Done! {result.output}\n\n```\n{json.dumps(result.data, indent=2, ensure_ascii=False)}\n```"
                return f"Done! {result.output}"
            else:
                return f"Error: {result.error}"

        except Exception as e:
            logger.error(f"Skill execution failed: {e}")
            return f"Execution failed: {str(e)}"

    async def process_with_context(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> str:
        """Process a message with additional context."""
        full_message = f"Context: {context}\n\nUser: {message}"
        result = await self.process(full_message, context.get("session_id", "default"))
        return result["response"]

    async def clear_session(self, session_id: str) -> None:
        """Clear a session's history."""
        await session_manager.clear_history(session_id)

    async def get_session_info(self, session_id: str) -> Dict[str, any]:
        """Get information about a session."""
        info = await session_manager.get_session_info(session_id)
        return info or {"error": "Session not found"}


async def run_chat_execution(
    agent: "Agent",
    *,
    message: str,
    session_id: str,
    user_name: Optional[str] = None,
    track_usage: bool = True,
    reasoning_replay: Optional[bool] = None,
    stream_callback: Optional[Any] = None,
    attached_images: Optional[List[str]] = None,
    attachments: Optional[List[str]] = None,
    portal_user_id: Optional[str] = None,
    portal_user_name: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Thin adapter for unified runtime bus chat execution."""
    process_kwargs: Dict[str, Any] = {
        "message": message,
        "session_id": session_id,
        "user_name": user_name,
        "track_usage": track_usage,
        "reasoning_replay": reasoning_replay,
        "stream_callback": stream_callback,
        "attached_images": attached_images,
        "attachments": attachments,
        "portal_user_id": portal_user_id,
        "portal_user_name": portal_user_name,
        "request_id": request_id,
    }
    result = await agent.process(**process_kwargs)
    context_state = await apply_progressive_context_after_turn(
        session_id=session_id,
        model=getattr(agent, "model", None),
    )
    if isinstance(result, dict):
        request_budget = result.get("request_budget") if isinstance(result.get("request_budget"), dict) else {}
        context_state = _merge_request_budget_into_context_state(context_state or {}, request_budget)
        effective_request_id = request_id
        if not effective_request_id:
            candidate_request_id = result.get("request_id")
            if candidate_request_id:
                effective_request_id = str(candidate_request_id)

        result["context_state"] = context_state
        if _is_meaningful_context_state(context_state):
            runtime_events = result.get("runtime_events")
            if not isinstance(runtime_events, list):
                runtime_events = []
                result["runtime_events"] = runtime_events

            if not _has_terminal_post_turn_context_snapshot(runtime_events):
                status = "failed" if result.get("error") else "completed"
                terminal_context_event = _build_terminal_context_snapshot_event(
                    context_state=context_state,
                    session_id=session_id,
                    agent_id=getattr(agent, "agent_id", None),
                    request_id=effective_request_id,
                    status=status,
                )
                if terminal_context_event:
                    runtime_events.append(terminal_context_event)
        if effective_request_id:
            result.setdefault("request_id", effective_request_id)
    return result


# Global agent instance
agent = Agent()
