"""Shared response-flow policy for planning, staging, and ASK_USER behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_RESPONSE_FLOW_CONFIG: Dict[str, Any] = {
    "plan_policy": "explicit_or_complex",
    "staging_policy": "explicit_or_complex",
    "default_skill_execution_style": "direct",
    "ask_user_policy": "blocked_only",
    "complexity_prompt_budget_ratio": 0.85,
    "complexity_min_request_tokens": 24000,
}

_PLAN_PHRASES = (
    "plan first",
    "preview first",
    "先给计划",
    "先做计划",
    "先预览",
    "先给大纲",
    "先不要直接生成",
)

_STAGING_PHRASES = (
    "one file at a time",
    "file by file",
    "phase by phase",
    "manifest first",
    "split into phases",
    "先出清单再继续",
    "一份一份生成",
    "一个文件一个文件",
    "按阶段生成",
)


@dataclass(frozen=True)
class ResponseFlowDecision:
    plan_required: bool
    staging_required: bool
    execution_style: str
    ask_user_policy: str
    reasons: List[str]


def resolve_response_flow_config(raw_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    resolved = dict(DEFAULT_RESPONSE_FLOW_CONFIG)
    if not isinstance(raw_cfg, dict):
        return resolved
    for key in DEFAULT_RESPONSE_FLOW_CONFIG:
        if key in raw_cfg and raw_cfg[key] is not None:
            resolved[key] = raw_cfg[key]
    try:
        resolved["complexity_prompt_budget_ratio"] = float(resolved["complexity_prompt_budget_ratio"])
    except Exception:
        resolved["complexity_prompt_budget_ratio"] = DEFAULT_RESPONSE_FLOW_CONFIG["complexity_prompt_budget_ratio"]
    try:
        resolved["complexity_min_request_tokens"] = int(resolved["complexity_min_request_tokens"])
    except Exception:
        resolved["complexity_min_request_tokens"] = DEFAULT_RESPONSE_FLOW_CONFIG["complexity_min_request_tokens"]
    return resolved


def has_explicit_plan_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    return any(phrase in text for phrase in _PLAN_PHRASES)


def has_explicit_staging_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    return any(phrase in text for phrase in _STAGING_PHRASES)


def is_truly_complex_request(
    *,
    request_estimated_tokens: Optional[int],
    prompt_budget_tokens: Optional[int],
    complexity_prompt_budget_ratio: float,
    complexity_min_request_tokens: int,
) -> bool:
    try:
        request_tokens = int(request_estimated_tokens or 0)
        budget_tokens = int(prompt_budget_tokens or 0)
    except Exception:
        return False
    if request_tokens <= 0 or budget_tokens <= 0:
        return False
    if request_tokens > budget_tokens:
        return True
    near_limit = request_tokens >= complexity_prompt_budget_ratio * budget_tokens
    meets_floor = request_tokens >= complexity_min_request_tokens
    return bool(near_limit and meets_floor)


def decide_response_flow(
    *,
    config_block: Optional[Dict[str, Any]] = None,
    user_text: str = "",
    request_estimated_tokens: Optional[int] = None,
    prompt_budget_tokens: Optional[int] = None,
    planning_mode: str = "auto",
    staging_mode: str = "auto",
    execution_style: str = "",
    ask_user_policy: str = "",
    generation_state_mode: str = "",
    max_output_recovery_active: bool = False,
) -> ResponseFlowDecision:
    cfg = resolve_response_flow_config(config_block)
    reasons: List[str] = []
    explicit_plan = has_explicit_plan_request(user_text)
    explicit_staging = has_explicit_staging_request(user_text)
    complex_request = is_truly_complex_request(
        request_estimated_tokens=request_estimated_tokens,
        prompt_budget_tokens=prompt_budget_tokens,
        complexity_prompt_budget_ratio=float(cfg["complexity_prompt_budget_ratio"]),
        complexity_min_request_tokens=int(cfg["complexity_min_request_tokens"]),
    )

    resolved_execution_style = execution_style if execution_style in {"direct", "stepwise"} else str(
        cfg.get("default_skill_execution_style") or "direct"
    )
    if resolved_execution_style == "stepwise":
        reasons.append("skill_execution_stepwise")

    resolved_ask_policy = ask_user_policy if ask_user_policy in {"blocked_only", "permissive"} else str(
        cfg.get("ask_user_policy") or "blocked_only"
    )

    plan_required = False
    plan_policy = str(cfg.get("plan_policy") or "explicit_or_complex")
    if planning_mode == "required":
        plan_required = True
        reasons.append("skill_metadata")
    elif planning_mode == "off":
        plan_required = False
    elif plan_policy == "always":
        plan_required = True
        reasons.append("config_policy")
    elif plan_policy == "never":
        plan_required = False
    else:
        if explicit_plan:
            plan_required = True
            reasons.append("explicit_user_request")
        elif complex_request:
            plan_required = True
            reasons.append("complexity_budget")
        elif resolved_execution_style == "stepwise":
            plan_required = True
            reasons.append("skill_metadata")

    staging_required = False
    staging_policy = str(cfg.get("staging_policy") or "explicit_or_complex")
    if staging_mode == "required":
        staging_required = True
        reasons.append("skill_metadata")
    elif staging_mode == "off":
        staging_required = False
    elif generation_state_mode == "staged":
        staging_required = True
        reasons.append("generation_state")
    elif max_output_recovery_active:
        staging_required = True
        reasons.append("max_output_recovery")
    elif staging_policy == "always":
        staging_required = True
        reasons.append("config_policy")
    elif staging_policy == "never":
        staging_required = False
    else:
        if explicit_staging:
            staging_required = True
            reasons.append("explicit_user_request")
        elif complex_request:
            staging_required = True
            reasons.append("complexity_budget")

    return ResponseFlowDecision(
        plan_required=plan_required,
        staging_required=staging_required,
        execution_style=resolved_execution_style,
        ask_user_policy=resolved_ask_policy,
        reasons=sorted(set(reasons)),
    )
