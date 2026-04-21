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
