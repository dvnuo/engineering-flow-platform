from src.runtime.response_flow_policy import decide_response_flow, resolve_skill_behavior_defaults


def test_explicit_plan_request_chinese_sets_plan_required():
    decision = decide_response_flow(user_text="先给计划，再继续")
    assert decision.plan_required is True
    assert decision.staging_required is False


def test_explicit_staged_request_chinese_sets_staging_required():
    decision = decide_response_flow(user_text="请一份一份生成")
    assert decision.staging_required is True


def test_generic_generate_implementation_not_staged_without_budget_pressure():
    decision = decide_response_flow(user_text="generate implementation")
    assert decision.staging_required is False


def test_generic_step_by_step_does_not_force_staging():
    decision = decide_response_flow(user_text="Please do this step by step")
    assert decision.staging_required is False


def test_generic_zh_stepwise_does_not_force_staging():
    decision = decide_response_flow(user_text="请逐步处理")
    assert decision.staging_required is False


def test_skill_metadata_staging_required_sets_staging():
    decision = decide_response_flow(user_text="", staging_mode="required")
    assert decision.staging_required is True


def test_budget_near_limit_sets_staging_required():
    decision = decide_response_flow(
        user_text="build output",
        request_estimated_tokens=30000,
        prompt_budget_tokens=32000,
    )
    assert decision.staging_required is True


def test_resolve_skill_behavior_defaults_uses_config_active_skill_conflict_policy():
    execution_style, ask_policy, conflict_policy = resolve_skill_behavior_defaults(
        {"active_skill_conflict_policy": "always_ask"}
    )
    assert execution_style == "direct"
    assert ask_policy == "blocked_only"
    assert conflict_policy == "always_ask"


def test_resolve_skill_behavior_defaults_skill_explicit_values_override_config():
    execution_style, ask_policy, conflict_policy = resolve_skill_behavior_defaults(
        {
            "default_skill_execution_style": "stepwise",
            "ask_user_policy": "permissive",
            "active_skill_conflict_policy": "auto_switch_direct",
        },
        execution_style="direct",
        ask_user_policy="blocked_only",
        active_skill_conflict_policy="always_ask",
    )
    assert execution_style == "direct"
    assert ask_policy == "blocked_only"
    assert conflict_policy == "always_ask"


def test_resolve_skill_behavior_defaults_skill_omission_uses_config_defaults():
    execution_style, ask_policy, conflict_policy = resolve_skill_behavior_defaults(
        {
            "default_skill_execution_style": "stepwise",
            "ask_user_policy": "permissive",
            "active_skill_conflict_policy": "always_ask",
        }
    )
    assert execution_style == "stepwise"
    assert ask_policy == "permissive"
    assert conflict_policy == "always_ask"

from src.runtime.response_flow_policy import DEFAULT_RESPONSE_FLOW_CONFIG, resolve_response_flow_config


def test_resolve_response_flow_config_none_returns_default_copy():
    resolved = resolve_response_flow_config(None)
    assert resolved == DEFAULT_RESPONSE_FLOW_CONFIG
    assert resolved is not DEFAULT_RESPONSE_FLOW_CONFIG


def test_resolve_response_flow_config_empty_returns_default_copy():
    resolved = resolve_response_flow_config({})
    assert resolved == DEFAULT_RESPONSE_FLOW_CONFIG
    assert resolved is not DEFAULT_RESPONSE_FLOW_CONFIG


def test_resolve_response_flow_config_mutation_does_not_change_defaults():
    resolved = resolve_response_flow_config(None)
    resolved["plan_policy"] = "always"
    assert DEFAULT_RESPONSE_FLOW_CONFIG["plan_policy"] == "explicit_or_complex"


def test_resolve_response_flow_config_partial_overlay_only_overrides_target_field():
    resolved = resolve_response_flow_config({"plan_policy": "always"})
    assert resolved["plan_policy"] == "always"
    assert resolved["staging_policy"] == DEFAULT_RESPONSE_FLOW_CONFIG["staging_policy"]
    assert (
        resolved["default_skill_execution_style"]
        == DEFAULT_RESPONSE_FLOW_CONFIG["default_skill_execution_style"]
    )
    assert resolved["ask_user_policy"] == DEFAULT_RESPONSE_FLOW_CONFIG["ask_user_policy"]
    assert (
        resolved["active_skill_conflict_policy"]
        == DEFAULT_RESPONSE_FLOW_CONFIG["active_skill_conflict_policy"]
    )


def test_resolve_response_flow_config_invalid_complexity_values_fall_back_to_defaults():
    resolved = resolve_response_flow_config(
        {"complexity_prompt_budget_ratio": "not-a-number", "complexity_min_request_tokens": "bad"}
    )
    assert (
        resolved["complexity_prompt_budget_ratio"]
        == DEFAULT_RESPONSE_FLOW_CONFIG["complexity_prompt_budget_ratio"]
    )
    assert (
        resolved["complexity_min_request_tokens"]
        == DEFAULT_RESPONSE_FLOW_CONFIG["complexity_min_request_tokens"]
    )
