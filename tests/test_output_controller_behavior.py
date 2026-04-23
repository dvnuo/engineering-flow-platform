import pytest


@pytest.mark.asyncio
async def test_keyword_heavy_request_does_not_force_staged_mode_initially():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        async def responses(self, **kwargs):
            return {"content": "ok", "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    _, diag = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate implementation", "tools": []},
        session_id="s-keyword",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate test cases for jira issue",
    )
    assert diag.get("generation_mode") == "normal"
    assert state["budget"].get("generation_mode") == "normal"


def test_non_staged_guard_does_not_enforce_manifest_phase_only():
    from src.runtime.output_controller import build_output_guard

    guard = build_output_guard(risk="medium", generation_mode="normal")
    assert "manifest/phase output only" not in guard.lower()
    assert "staged mode is enforced" not in guard.lower()


@pytest.mark.asyncio
async def test_max_output_recovery_enters_staged_and_continue_advances():
    from src.runtime.output_controller import call_llm_with_output_control

    class _Client:
        def __init__(self):
            self.calls = 0

        async def responses(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"error": {"code": "max_output_tokens_exceeded", "message": "x"}}
            return {"content": "manifest", "tool_calls": [], "function_calls": [], "usage": {}}

    state = {"budget": {}}
    _, diag1 = await call_llm_with_output_control(
        llm_client=_Client(),
        llm_kwargs={"input_items": [], "system_prompt": "generate implementation", "tools": []},
        session_id="s-recovery",
        stage="tool_loop",
        context_state=state,
        latest_user_text="generate full implementation",
    )
    assert diag1["max_output_recovery"]["applied"] is True
    assert state["budget"]["generation_mode"] == "staged"

    class _ContinueClient:
        async def responses(self, **kwargs):
            return {"content": "phase", "tool_calls": [], "function_calls": [], "usage": {}}

    _, diag2 = await call_llm_with_output_control(
        llm_client=_ContinueClient(),
        llm_kwargs={"input_items": [], "system_prompt": "continue", "tools": []},
        session_id="s-recovery",
        stage="tool_loop",
        context_state=state,
        latest_user_text="continue",
    )
    assert diag2.get("generation", {}).get("generation_mode") == "staged"
    assert "manifest" in set(diag2.get("generation", {}).get("completed_phases", []))
