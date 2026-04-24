import pytest

from src.agents import core
from src.runtime.output_controller import build_output_guard, call_llm_with_output_control
from src.runtime.response_flow_policy import decide_response_flow


def test_process_regression_generic_generate_request_stays_direct_by_default():
    decision = decide_response_flow(
        user_text="generate implementation for auth service",
        request_estimated_tokens=3000,
        prompt_budget_tokens=32000,
    )
    assert decision.staging_required is False
    guard = build_output_guard(risk="normal", generation_mode="normal", staging_required=decision.staging_required)
    assert guard == ""


@pytest.mark.asyncio
async def test_process_regression_staged_state_does_not_stick_to_independent_new_request():
    class _Client:
        async def responses(self, **kwargs):
            return {"content": "ok", "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    _, first_diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "stage", "tools": []},
        session_id="s-regression",
        stage="tool_loop",
        context_state=state,
        latest_user_text="one file at a time",
    )
    assert first_diag.get("generation_mode") == "staged"

    _, second_diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "normal", "tools": []},
        session_id="s-regression",
        stage="tool_loop",
        context_state=state,
        latest_user_text="what is 2+2?",
    )
    assert second_diag.get("generation_mode") != "staged"
    assert second_diag.get("generation", {}).get("generation_mode") != "staged"
    assert state.get("budget", {}).get("generation_mode") != "staged"


def test_process_regression_stepwise_active_skill_does_not_swallow_independent_requests():
    contract = {
        "skill_name": "alpha",
        "status": "active",
        "execution_style": "stepwise",
        "planning_mode": "required",
        "staging_mode": "required",
    }
    registry = type("Registry", (), {"match_skill": lambda self, message: []})()
    assert core._should_continue_existing_active_skill(contract, "review this PR", registry) is False
    assert core._should_continue_existing_active_skill(contract, "写周报", registry) is False
