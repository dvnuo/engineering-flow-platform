"""Runtime output risk, staged guard, and max_output recovery controller."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.context_blob_store import put_text
from src.config import config, resolve_model_limits, resolve_output_boundary


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _default_output_chars(model: Optional[str] = None) -> Tuple[int, str]:
    boundary = resolve_output_boundary(model)
    chars = int(boundary.get("max_chat_output_chars") or 0)
    if chars > 0:
        return chars, str(boundary.get("output_boundary_source") or "model_limits_derived")
    tokens = int(boundary.get("max_chat_output_tokens") or 0)
    if tokens > 0:
        return tokens * int(boundary.get("chars_per_token_estimate") or 4), str(boundary.get("output_boundary_source") or "model_limits_derived")
    return 8000, "emergency_fallback_8000"


def normalize_chat_output_chars(
    candidate: Any,
    *,
    boundary: Dict[str, Any],
    allow_low: bool = False,
) -> Tuple[int, bool, Optional[str]]:
    chars_per_token = int(boundary.get("chars_per_token_estimate") or 4)
    derived = int(boundary.get("max_chat_output_chars") or 0)
    if derived <= 0:
        derived = int(boundary.get("max_chat_output_tokens") or 0) * chars_per_token
    if derived <= 0:
        derived = 8000
    configured_raw = None if candidate in (None, "") else str(candidate)
    parsed = _safe_int(candidate, derived)
    min_reasonable = max(1, int(derived * 0.25))
    if parsed < min_reasonable and not allow_low:
        return derived, configured_raw is not None, configured_raw
    return parsed, False, configured_raw


def resolve_effective_max_tokens_for_model(model: Optional[str]) -> Tuple[int, Dict[str, Any]]:
    llm_cfg = config.llm if isinstance(config.llm, dict) else {}
    limits = resolve_model_limits(model)
    model_max_tokens = int(limits.get("max_output_tokens") or llm_cfg.get("max_tokens") or 64000)
    configured_raw = llm_cfg.get("max_tokens")
    configured_max_tokens: Optional[int]
    try:
        configured_max_tokens = int(configured_raw) if configured_raw is not None else None
    except Exception:
        configured_max_tokens = None
    if configured_max_tokens is not None and configured_max_tokens <= 0:
        configured_max_tokens = None
    allow_lower = bool(llm_cfg.get("allow_lower_max_tokens_than_model_limit", False))
    legacy_ignored = False
    if configured_max_tokens is not None and configured_max_tokens < model_max_tokens and not allow_lower:
        effective_max_tokens = model_max_tokens
        legacy_ignored = True
    else:
        effective_max_tokens = min(model_max_tokens, int(configured_max_tokens or model_max_tokens))
    return effective_max_tokens, {
        "configured_max_tokens": configured_max_tokens,
        "effective_max_tokens": effective_max_tokens,
        "legacy_max_tokens_ignored": legacy_ignored,
        "allow_lower_max_tokens_than_model_limit": allow_lower,
    }


def classify_output_risk(user_text: str, active_skill: Any, system_prompt: str, source_state: Optional[Dict[str, Any]] = None) -> str:
    text = (str(user_text or "") + " " + str(system_prompt or "")).lower()
    skill_name = str(getattr(active_skill, "name", "") or getattr(active_skill, "skill_name", "")).lower()
    markers = ("generate", "test", "implementation", "spec", "feature", "all jira", "all confluence", "全部", "生成")
    if any(m in text for m in markers) or any(m in skill_name for m in markers):
        return "high"
    if len(text) > 1200:
        return "medium"
    return "normal"


def build_output_guard(risk: str, generation_mode: str) -> str:
    if generation_mode != "staged" and risk != "high":
        return ""
    return (
        "Large generation output guard: staged mode is enforced. "
        "Return manifest/phase output only; do not emit full multi-file content. "
        "Keep chat output concise and split multi-file output across phases."
    )


def is_max_output_truncated_result(result: Dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    err = result.get("error")
    if isinstance(err, dict) and err.get("code") == "max_output_tokens_exceeded":
        return True
    if result.get("truncated") is True:
        return True
    warning = result.get("warning")
    if isinstance(warning, dict):
        if warning.get("code") == "max_output_tokens_exceeded":
            return True
        if warning.get("type") == "truncated_response":
            return True
    details = result.get("incomplete_details")
    if isinstance(details, dict) and "max_output" in str(details.get("reason", "")).lower():
        return True
    return False


def get_generation_state(context_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(context_state, dict):
        return {}
    gen = context_state.get("generation")
    if not isinstance(gen, dict):
        gen = {}
        context_state["generation"] = gen
    return gen


def _default_completion_criteria(generation_mode: str) -> List[str]:
    if generation_mode == "staged":
        return ["manifest_prepared", "phase_output_recorded"]
    return ["response_bounded"]


def initialize_completion_criteria(gen: Dict[str, Any], *, generation_mode: str) -> Dict[str, bool]:
    raw = gen.get("completion_criteria")
    if isinstance(raw, list) and raw:
        criteria = [str(item) for item in raw if str(item).strip()]
    else:
        criteria = _default_completion_criteria(generation_mode)
    status = gen.get("completion_criteria_status")
    if not isinstance(status, dict):
        status = {}
    normalized: Dict[str, bool] = {}
    for criterion in criteria:
        normalized[criterion] = bool(status.get(criterion, False))
    gen["completion_criteria"] = criteria
    gen["completion_criteria_status"] = normalized
    gen["completion_criteria_count"] = len(criteria)
    return normalized


def _refresh_generation_done(gen: Dict[str, Any]) -> bool:
    status = gen.get("completion_criteria_status")
    done = bool(isinstance(status, dict) and status and all(bool(v) for v in status.values()))
    gen["generation_done"] = done
    return done


def mark_phase_complete(gen: Dict[str, Any], phase: str) -> None:
    completed = gen.get("completed_phases")
    if not isinstance(completed, list):
        completed = []
    if phase and phase not in completed:
        completed.append(phase)
    gen["completed_phases"] = completed


def next_incomplete_phase(gen: Dict[str, Any]) -> str:
    criteria = gen.get("completion_criteria")
    status = gen.get("completion_criteria_status")
    if not isinstance(criteria, list) or not criteria:
        return str(gen.get("next_phase") or "phase_1")
    if not isinstance(status, dict):
        status = {}
    index_map = {
        "manifest_prepared": "manifest",
        "phase_output_recorded": "phase_1",
        "response_bounded": "phase_1",
    }
    for criterion in criteria:
        key = str(criterion)
        if not bool(status.get(key, False)):
            return index_map.get(key, f"phase_{criteria.index(criterion) + 1}")
    return "complete"


def ensure_staged_generation(
    context_state: Optional[Dict[str, Any]],
    *,
    stage: str,
    max_chat_output_chars: Optional[int],
) -> Dict[str, Any]:
    gen = get_generation_state(context_state)
    completed = gen.get("completed_phases")
    if not isinstance(completed, list):
        completed = []
    gen["generation_mode"] = "staged"
    gen["current_generation_phase"] = str(gen.get("current_generation_phase") or "manifest")
    gen["completed_phases"] = completed
    gen["next_phase"] = str(gen.get("next_phase") or "phase_1")
    refs = gen.get("generated_artifact_refs")
    gen["generated_artifact_refs"] = refs if isinstance(refs, list) else []
    gen["generated_artifact_ref_count"] = len(gen["generated_artifact_refs"])
    artifacts_by_phase = gen.get("generated_artifacts_by_phase")
    gen["generated_artifacts_by_phase"] = artifacts_by_phase if isinstance(artifacts_by_phase, dict) else {}
    initialize_completion_criteria(gen, generation_mode="staged")
    coverage = gen.get("source_digest_chunk_coverage")
    gen["source_digest_chunk_coverage"] = coverage if isinstance(coverage, list) else []
    gen["source_digest_chunk_coverage_count"] = len(gen["source_digest_chunk_coverage"])
    _refresh_generation_done(gen)
    fallback_chars, _ = _default_output_chars()
    boundary = {
        "max_chat_output_chars": fallback_chars,
        "chars_per_token_estimate": 4,
    }
    normalized_chars, _, _ = normalize_chat_output_chars(
        max_chat_output_chars,
        boundary=boundary,
        allow_low=True,
    )
    gen["max_chat_output_chars"] = normalized_chars
    gen["output_controller_applied"] = True
    gen["output_controller_stage"] = stage
    return gen


def advance_generation_phase(context_state: Optional[Dict[str, Any]], *, latest_user_text: str = "") -> Dict[str, Any]:
    gen = get_generation_state(context_state)
    if not gen:
        return gen
    text = str(latest_user_text or "").strip().lower()
    if text not in {"continue", "next", "go on", "继续", "继续生成"}:
        return gen
    current = str(gen.get("current_generation_phase") or "manifest")
    mark_phase_complete(gen, current)
    next_phase = next_incomplete_phase(gen)
    gen["current_generation_phase"] = next_phase
    if next_phase.startswith("phase_"):
        try:
            idx = int(next_phase.split("_", 1)[1])
            gen["next_phase"] = f"phase_{idx + 1}"
        except Exception:
            gen["next_phase"] = "phase_next"
    elif next_phase == "complete":
        gen["next_phase"] = "complete"
    else:
        gen["next_phase"] = "phase_1"
    return gen


def _extract_content_text(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                txt = block.get("text")
                if isinstance(txt, str):
                    text_parts.append(txt)
        return "\n".join(text_parts).strip()
    return ""


def enforce_chat_output_bound(content: str, *, session_id: str, stage: str, max_chars: Optional[int] = None) -> Tuple[str, Dict[str, Any]]:
    text = str(content or "")
    fallback_chars, _ = _default_output_chars()
    max_chars = _safe_int(max_chars, fallback_chars)
    if len(text) <= max_chars:
        return text, {"bounded": False, "ref_count": 0}
    ref = put_text(
        session_id=session_id,
        kind="assistant_output",
        source_id=f"{stage}_oversized",
        title=f"Oversized assistant output ({stage})",
        content=text,
        metadata={"stage": stage},
    )
    bounded = (
        "I saved the oversized draft and will continue from the next phase.\n"
        "Output was too large for one response. Continuing in staged mode with concise manifest.\n"
        "generated_artifact_ref_count=1\nnext_phase=phase_1\n"
        f"Use context_read_ref(ref=\"{ref}\") if needed."
    )
    return bounded, {"bounded": True, "ref_count": 1, "generated_artifact_ref_count": 1, "ref": ref}


async def recover_max_output_tokens(
    *,
    llm_client: Any,
    llm_result: Dict[str, Any],
    llm_kwargs: Dict[str, Any],
    state: Optional[Dict[str, Any]],
    stage: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not is_max_output_truncated_result(llm_result):
        return llm_result, {"applied": False, "attempts": 0}

    partial_ref = None
    partial_content = _extract_content_text(llm_result if isinstance(llm_result, dict) else {})
    if partial_content:
        partial_ref = put_text(
            session_id=str((state or {}).get("session_id") or "unknown_session"),
            kind="assistant_output_partial",
            source_id=f"{stage}_max_output_partial",
            title=f"Partial output before recovery ({stage})",
            content=partial_content,
            metadata={"stage": stage, "reason": "max_output_tokens"},
        )

    retry_kwargs = dict(llm_kwargs)
    retry_kwargs["system_prompt"] = (
        (retry_kwargs.get("system_prompt") or "")
        + "\n\nRecovery mode: output exceeded max_output_tokens. Return concise staged manifest only."
    ).strip()
    retry_kwargs["input_items"] = list(retry_kwargs.get("input_items") or []) + [
        {"role": "user", "content": "Continue in staged mode only (manifest + next phase)."}
    ]
    retry_result = await llm_client.responses(**retry_kwargs)
    info = {"applied": True, "attempts": 1, "partial_ref": partial_ref}
    if isinstance(state, dict):
        state["max_output_recovery_applied"] = True
        state["max_output_recovery_attempts"] = int(state.get("max_output_recovery_attempts", 0)) + 1
        state["generation_mode"] = "staged"
        state["current_generation_phase"] = state.get("current_generation_phase") or "manifest"
        state["output_risk_level"] = "high"
        state["output_controller_recovery_reason"] = "max_output_tokens"
        if partial_ref:
            state["max_output_partial_ref"] = partial_ref
    if isinstance(retry_result, dict) and not retry_result.get("error") and not is_max_output_truncated_result(retry_result):
        return retry_result, info

    fallback_text = (
        "I saved the oversized draft and will continue from the next phase. "
        "I switched to staged generation with manifest-first output."
    )
    if partial_ref:
        fallback_text += f"\nRecovered partial output was saved. Use context_read_ref(ref=\"{partial_ref}\") if needed."
    fallback = {
        "content": fallback_text,
        "tool_calls": [],
        "function_calls": [],
        "usage": llm_result.get("usage", {}) if isinstance(llm_result, dict) else {},
    }
    return fallback, info


async def call_llm_with_output_control(
    *,
    llm_client: Any,
    llm_kwargs: Dict[str, Any],
    session_id: str,
    stage: str,
    context_state: Optional[Dict[str, Any]],
    active_skill: Any = None,
    latest_user_text: str = "",
    max_chat_output_chars: Optional[int] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    model = str(llm_kwargs.get("model") or "")
    llm_kwargs = dict(llm_kwargs)
    boundary = resolve_output_boundary(model)
    effective_max_tokens, max_token_diag = resolve_effective_max_tokens_for_model(model or None)
    caller_max_tokens_raw = llm_kwargs.get("max_tokens")
    caller_max_tokens: Optional[int]
    try:
        caller_max_tokens = int(caller_max_tokens_raw) if caller_max_tokens_raw is not None else None
    except Exception:
        caller_max_tokens = None
    allow_lower_max_tokens = bool(max_token_diag.get("allow_lower_max_tokens_than_model_limit", False))
    caller_max_tokens_ignored = False
    model_token_cap = int(boundary.get("max_output_tokens") or effective_max_tokens)
    if caller_max_tokens is None:
        llm_kwargs["max_tokens"] = int(effective_max_tokens)
    elif caller_max_tokens <= 0:
        llm_kwargs["max_tokens"] = int(effective_max_tokens)
        caller_max_tokens_ignored = True
    elif caller_max_tokens < int(effective_max_tokens) and not allow_lower_max_tokens:
        llm_kwargs["max_tokens"] = int(effective_max_tokens)
        caller_max_tokens_ignored = True
    else:
        llm_kwargs["max_tokens"] = int(min(caller_max_tokens, model_token_cap))
    budget = context_state.setdefault("budget", {}) if isinstance(context_state, dict) else {}
    fallback_chars, fallback_source = _default_output_chars(model)
    allow_low = bool(boundary.get("allow_low_max_chat_output_chars", False))
    max_chat_output_chars, arg_ignored, configured_arg_chars = normalize_chat_output_chars(
        max_chat_output_chars,
        boundary=boundary,
        allow_low=allow_low,
    )
    budget_chars_ignored = False
    configured_budget_chars = None
    if isinstance(budget, dict):
        budget_chars = budget.get("max_chat_output_chars")
        budget_tokens = budget.get("max_chat_output_tokens")
        if budget_chars not in (None, ""):
            max_chat_output_chars, ignored, configured_budget_chars = normalize_chat_output_chars(
                budget_chars,
                boundary=boundary,
                allow_low=allow_low,
            )
            budget_chars_ignored = budget_chars_ignored or ignored
        if budget_tokens not in (None, "") and budget.get("max_chat_output_chars") in (None, ""):
            chars_per_token = int(boundary.get("chars_per_token_estimate") or 4)
            tokens_candidate = _safe_int(budget_tokens, int(boundary.get("max_chat_output_tokens") or 0)) * chars_per_token
            max_chat_output_chars, ignored, _ = normalize_chat_output_chars(
                tokens_candidate,
                boundary=boundary,
                allow_low=allow_low,
            )
            budget_chars_ignored = budget_chars_ignored or ignored
    diagnostics: Dict[str, Any] = {"output_controller_applied": True, "stage": stage}
    diagnostics["output_boundary_source"] = str(boundary.get("output_boundary_source") or fallback_source)
    diagnostics["legacy_max_chat_output_chars_ignored"] = bool(boundary.get("legacy_max_chat_output_chars_ignored", False))
    diagnostics["configured_max_chat_output_chars"] = boundary.get("configured_max_chat_output_chars")
    diagnostics["max_context_window_tokens"] = int(boundary.get("max_context_window_tokens") or 0)
    diagnostics["max_prompt_tokens"] = int(boundary.get("max_prompt_tokens") or 0)
    diagnostics["max_output_tokens"] = int(boundary.get("max_output_tokens") or 0)
    diagnostics["max_chat_output_tokens"] = int(boundary.get("max_chat_output_tokens") or 0)
    diagnostics["max_chat_output_chars"] = max_chat_output_chars
    diagnostics["chars_per_token_estimate"] = int(boundary.get("chars_per_token_estimate") or 4)
    diagnostics["budget_max_chat_output_chars_ignored"] = budget_chars_ignored
    diagnostics["configured_budget_max_chat_output_chars"] = configured_budget_chars
    diagnostics["arg_max_chat_output_chars_ignored"] = arg_ignored
    diagnostics["configured_arg_max_chat_output_chars"] = configured_arg_chars
    diagnostics["configured_max_tokens"] = max_token_diag.get("configured_max_tokens")
    diagnostics["effective_max_tokens"] = int(llm_kwargs.get("max_tokens") or effective_max_tokens)
    diagnostics["legacy_max_tokens_ignored"] = bool(max_token_diag.get("legacy_max_tokens_ignored") or caller_max_tokens_ignored)
    diagnostics["caller_max_tokens"] = caller_max_tokens
    diagnostics["caller_max_tokens_ignored"] = caller_max_tokens_ignored
    if isinstance(budget, dict):
        budget["session_id"] = session_id

    risk = classify_output_risk(
        user_text=latest_user_text,
        active_skill=active_skill,
        system_prompt=str(llm_kwargs.get("system_prompt") or ""),
        source_state=context_state,
    )
    generation_mode = "staged" if risk == "high" else str((budget or {}).get("generation_mode") or "normal")
    gen_state = get_generation_state(context_state)
    if generation_mode == "staged" or (isinstance(gen_state, dict) and gen_state.get("generation_mode") == "staged"):
        generation_mode = "staged"
        gen_state = ensure_staged_generation(context_state, stage=stage, max_chat_output_chars=max_chat_output_chars)
        initialize_completion_criteria(gen_state, generation_mode="staged")
        gen_state = advance_generation_phase(context_state, latest_user_text=latest_user_text)
    guard = build_output_guard(risk=risk, generation_mode=generation_mode)
    if guard:
        llm_kwargs = dict(llm_kwargs)
        llm_kwargs["system_prompt"] = ((llm_kwargs.get("system_prompt") or "") + "\n\n" + guard).strip()
    diagnostics.update({"output_risk_level": risk, "generation_mode": generation_mode, "guard_applied": bool(guard)})
    if isinstance(gen_state, dict) and gen_state:
        diagnostics["generation"] = dict(gen_state)

    if isinstance(budget, dict):
        budget["output_controller_applied"] = True
        budget["output_risk_level"] = risk
        budget["generation_mode"] = generation_mode
        budget["output_token_limit"] = int(llm_kwargs.get("max_tokens") or boundary.get("max_output_tokens") or 0)
        budget["max_chat_output_tokens"] = int(boundary.get("max_chat_output_tokens") or 0)
        budget["max_chat_output_chars"] = max_chat_output_chars
        budget["max_context_window_tokens"] = int(boundary.get("max_context_window_tokens") or 0)
        budget["max_prompt_tokens"] = int(boundary.get("max_prompt_tokens") or 0)
        budget["max_output_tokens"] = int(boundary.get("max_output_tokens") or 0)
        budget["chars_per_token_estimate"] = int(boundary.get("chars_per_token_estimate") or 4)
        budget["configured_max_chat_output_chars"] = boundary.get("configured_max_chat_output_chars")
        budget["legacy_max_chat_output_chars_ignored"] = bool(boundary.get("legacy_max_chat_output_chars_ignored", False))
        budget["budget_max_chat_output_chars_ignored"] = budget_chars_ignored
        budget["configured_budget_max_chat_output_chars"] = configured_budget_chars
        budget["output_boundary_source"] = str(boundary.get("output_boundary_source") or fallback_source)
        budget["output_controller_stage"] = stage
        budget["configured_max_tokens"] = max_token_diag.get("configured_max_tokens")
        budget["effective_max_tokens"] = int(llm_kwargs.get("max_tokens") or effective_max_tokens)
        budget["legacy_max_tokens_ignored"] = bool(max_token_diag.get("legacy_max_tokens_ignored") or caller_max_tokens_ignored)
        budget["caller_max_tokens"] = caller_max_tokens
        budget["caller_max_tokens_ignored"] = caller_max_tokens_ignored

    llm_result = await llm_client.responses(**llm_kwargs)
    llm_result, recovery_info = await recover_max_output_tokens(
        llm_client=llm_client,
        llm_result=llm_result if isinstance(llm_result, dict) else {},
        llm_kwargs=llm_kwargs,
        state=budget if isinstance(budget, dict) else context_state,
        stage=stage,
    )
    diagnostics["max_output_recovery"] = recovery_info
    if isinstance(gen_state, dict):
        gen_state["output_controller_applied"] = True
        if recovery_info.get("applied"):
            gen_state["generation_mode"] = "staged"
            gen_state["output_controller_recovery_reason"] = "max_output_tokens"
            status = gen_state.get("completion_criteria_status")
            if isinstance(status, dict):
                status["manifest_prepared"] = False

    content = _extract_content_text(llm_result)
    if content:
        if isinstance(gen_state, dict):
            status = gen_state.get("completion_criteria_status")
            if isinstance(status, dict):
                phase = str(gen_state.get("current_generation_phase") or "manifest")
                if phase == "manifest":
                    status["manifest_prepared"] = True
                if phase.startswith("phase_"):
                    status["phase_output_recorded"] = True
            _refresh_generation_done(gen_state)
        bounded, bound_info = enforce_chat_output_bound(
            content,
            session_id=session_id or "unknown_session",
            stage=stage,
            max_chars=max_chat_output_chars,
        )
        if bound_info.get("bounded"):
            llm_result = dict(llm_result)
            llm_result["content"] = bounded
            diagnostics["oversized_output_saved"] = True
        diagnostics["output_bounding"] = bound_info
        if isinstance(budget, dict):
            budget["output_bounded"] = bool(bound_info.get("bounded"))
            budget["max_chat_output_chars"] = max_chat_output_chars
            budget["oversized_output_saved"] = bool(bound_info.get("bounded"))
            budget["partial_output_saved"] = bool(recovery_info.get("partial_ref"))
        if isinstance(gen_state, dict):
            gen_state["max_chat_output_chars"] = max_chat_output_chars
            if bound_info.get("bounded"):
                ref = bound_info.get("ref")
                if isinstance(ref, str):
                    refs = gen_state.get("generated_artifact_refs")
                    if not isinstance(refs, list):
                        refs = []
                    refs.append(ref)
                    gen_state["generated_artifact_refs"] = refs
                    gen_state["generated_artifact_ref_count"] = len(refs)
                    by_phase = gen_state.get("generated_artifacts_by_phase")
                    if not isinstance(by_phase, dict):
                        by_phase = {}
                    phase = str(gen_state.get("current_generation_phase") or "manifest")
                    phase_refs = by_phase.get(phase)
                    if not isinstance(phase_refs, list):
                        phase_refs = []
                    phase_refs.append(ref)
                    by_phase[phase] = phase_refs
                    gen_state["generated_artifacts_by_phase"] = by_phase
                    coverage = gen_state.get("source_digest_chunk_coverage")
                    if not isinstance(coverage, list):
                        coverage = []
                    coverage.append(f"{stage}:{phase}")
                    gen_state["source_digest_chunk_coverage"] = coverage
                    gen_state["source_digest_chunk_coverage_count"] = len(coverage)
                status = gen_state.get("completion_criteria_status")
                if isinstance(status, dict):
                    status["response_bounded"] = True
                    phase = str(gen_state.get("current_generation_phase") or "manifest")
                    if phase == "manifest":
                        status["manifest_prepared"] = True
                    if phase.startswith("phase_"):
                        status["phase_output_recorded"] = True
                if gen_state.get("generation_mode") == "staged":
                    gen_state["next_phase"] = next_incomplete_phase(gen_state)
                    if gen_state["next_phase"] == "complete":
                        gen_state["current_generation_phase"] = "complete"
                    else:
                        gen_state["current_generation_phase"] = gen_state["next_phase"]
            gen_state["partial_output_saved"] = bool(recovery_info.get("partial_ref"))
            _refresh_generation_done(gen_state)

    if isinstance(gen_state, dict):
        diagnostics["generation"] = dict(gen_state)
        diagnostics["generated_artifact_ref_count"] = int(gen_state.get("generated_artifact_ref_count") or 0)
        diagnostics["generation_done"] = bool(gen_state.get("generation_done", False))
    return llm_result, diagnostics
