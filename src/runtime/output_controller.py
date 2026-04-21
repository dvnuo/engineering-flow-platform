"""Runtime output risk, staged guard, and max_output recovery controller."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

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
    error = llm_result.get("error") if isinstance(llm_result, dict) else None
    if not isinstance(error, dict) or error.get("code") != "max_output_tokens_exceeded":
        return llm_result, {"applied": False, "attempts": 0}

    retry_kwargs = dict(llm_kwargs)
    retry_kwargs["system_prompt"] = (
        (retry_kwargs.get("system_prompt") or "")
        + "\n\nRecovery mode: output exceeded max_output_tokens. Return concise staged manifest only."
    ).strip()
    retry_kwargs["input_items"] = list(retry_kwargs.get("input_items") or []) + [
        {"role": "user", "content": "Continue in staged mode only (manifest + next phase)."}
    ]
    retry_result = await llm_client.responses(**retry_kwargs)
    info = {"applied": True, "attempts": 1}
    if isinstance(state, dict):
        state["max_output_recovery_applied"] = True
        state["max_output_recovery_attempts"] = int(state.get("max_output_recovery_attempts", 0)) + 1
        state["generation_mode"] = "staged"
        state["current_generation_phase"] = state.get("current_generation_phase") or "manifest"
        state["output_risk_level"] = "high"
    if isinstance(retry_result, dict) and not retry_result.get("error"):
        return retry_result, info

    fallback = {
        "content": (
            "The requested output is too large for one response. I switched to staged generation and "
            "will continue with manifest-first output."
        ),
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

    risk = classify_output_risk(
        user_text=latest_user_text,
        active_skill=active_skill,
        system_prompt=str(llm_kwargs.get("system_prompt") or ""),
        source_state=context_state,
    )
    generation_mode = "staged" if risk == "high" else str((budget or {}).get("generation_mode") or "normal")
    guard = build_output_guard(risk=risk, generation_mode=generation_mode)
    if guard:
        llm_kwargs = dict(llm_kwargs)
        llm_kwargs["system_prompt"] = ((llm_kwargs.get("system_prompt") or "") + "\n\n" + guard).strip()
    diagnostics.update({"output_risk_level": risk, "generation_mode": generation_mode, "guard_applied": bool(guard)})

    if isinstance(budget, dict):
        budget["output_controller_applied"] = True
        budget["output_risk_level"] = risk
        budget["generation_mode"] = generation_mode

    llm_result = await llm_client.responses(**llm_kwargs)
    llm_result, recovery_info = await recover_max_output_tokens(
        llm_client=llm_client,
        llm_result=llm_result if isinstance(llm_result, dict) else {},
        llm_kwargs=llm_kwargs,
        state=budget if isinstance(budget, dict) else context_state,
        stage=stage,
    )
    diagnostics["max_output_recovery"] = recovery_info

    content = _extract_content_text(llm_result)
    if content and generation_mode == "staged":
        bounded, bound_info = enforce_chat_output_bound(
            content,
            session_id=session_id or "unknown_session",
            stage=stage,
            max_chars=max_chat_output_chars,
        )
        if bound_info.get("bounded"):
            llm_result = dict(llm_result)
            llm_result["content"] = bounded
        diagnostics["output_bounding"] = bound_info
        if isinstance(budget, dict):
            budget["output_bounded"] = bool(bound_info.get("bounded"))
            budget["max_chat_output_chars"] = int(max_chat_output_chars)

    return llm_result, diagnostics
