from src.runtime.response_flow_policy import decide_response_flow


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
