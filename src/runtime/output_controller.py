"""Runtime output risk, staged guard, and max_output recovery controller."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.context_blob_store import put_text


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
        "Keep chat output <= 8000 characters."
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


def ensure_staged_generation(
    context_state: Optional[Dict[str, Any]],
    *,
    stage: str,
    max_chat_output_chars: int,
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
    criteria = gen.get("completion_criteria")
    gen["completion_criteria"] = criteria if isinstance(criteria, list) else ["manifest_ready", "phase_outputs_recorded"]
    coverage = gen.get("source_digest_chunk_coverage")
    gen["source_digest_chunk_coverage"] = coverage if isinstance(coverage, list) else []
    gen["completion_criteria_count"] = len(gen["completion_criteria"])
    gen["source_digest_chunk_coverage_count"] = len(gen["source_digest_chunk_coverage"])
    gen["generation_done"] = bool(gen.get("generation_done", False))
    gen["max_chat_output_chars"] = int(max_chat_output_chars)
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
    completed: List[str] = [str(p) for p in (gen.get("completed_phases") or [])]
    if current not in completed:
        completed.append(current)
    next_phase = str(gen.get("next_phase") or "phase_1")
    gen["completed_phases"] = completed
    gen["current_generation_phase"] = next_phase
    if next_phase.startswith("phase_"):
        try:
            idx = int(next_phase.split("_", 1)[1])
            gen["next_phase"] = f"phase_{idx + 1}"
        except Exception:
            gen["next_phase"] = "phase_next"
    else:
        gen["next_phase"] = "phase_next"
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


def enforce_chat_output_bound(content: str, *, session_id: str, stage: str, max_chars: int = 8000) -> Tuple[str, Dict[str, Any]]:
    text = str(content or "")
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
        "Output was too large for one response. Saved full draft to context blob. "
        "Continuing in staged mode with concise manifest.\n"
        "ref_count=1\nnext_phase=phase_1\n"
        f"Use context_read_ref(ref=\"{ref}\") if needed."
    )
    return bounded, {"bounded": True, "ref_count": 1, "ref": ref}


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
    if isinstance(retry_result, dict) and not retry_result.get("error"):
        return retry_result, info

    fallback_text = (
        "The requested output is too large for one response. I switched to staged generation and "
        "will continue with manifest-first output."
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
    max_chat_output_chars: int = 8000,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    diagnostics: Dict[str, Any] = {"output_controller_applied": True, "stage": stage}
    budget = context_state.setdefault("budget", {}) if isinstance(context_state, dict) else {}
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
        budget["output_token_limit"] = int(llm_kwargs.get("max_tokens") or 0)
        budget["output_controller_stage"] = stage

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

    content = _extract_content_text(llm_result)
    if content:
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
            budget["max_chat_output_chars"] = int(max_chat_output_chars)
            budget["oversized_output_saved"] = bool(bound_info.get("bounded"))
            budget["partial_output_saved"] = bool(recovery_info.get("partial_ref"))
        if isinstance(gen_state, dict):
            gen_state["max_chat_output_chars"] = int(max_chat_output_chars)
            if bound_info.get("bounded"):
                ref = bound_info.get("ref")
                if isinstance(ref, str):
                    refs = gen_state.get("generated_artifact_refs")
                    if not isinstance(refs, list):
                        refs = []
                    refs.append(ref)
                    gen_state["generated_artifact_refs"] = refs
                    gen_state["generated_artifact_ref_count"] = len(refs)
                    coverage = gen_state.get("source_digest_chunk_coverage")
                    if not isinstance(coverage, list):
                        coverage = []
                    coverage.append(f"{stage}:{gen_state.get('current_generation_phase')}")
                    gen_state["source_digest_chunk_coverage"] = coverage
                    gen_state["source_digest_chunk_coverage_count"] = len(coverage)
                if gen_state.get("generation_mode") == "staged":
                    gen_state["next_phase"] = "phase_1"
            gen_state["partial_output_saved"] = bool(recovery_info.get("partial_ref"))
            if gen_state.get("generated_artifact_ref_count", 0) >= 2:
                gen_state["generation_done"] = True

    if isinstance(gen_state, dict):
        diagnostics["generation"] = dict(gen_state)
        diagnostics["generated_artifact_ref_count"] = int(gen_state.get("generated_artifact_ref_count") or 0)
        diagnostics["generation_done"] = bool(gen_state.get("generation_done", False))
    return llm_result, diagnostics
