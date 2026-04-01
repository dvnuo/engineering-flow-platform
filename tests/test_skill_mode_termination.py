import pytest
from types import SimpleNamespace

from src.agents.core import Agent, _is_lookup_only_skill
from src.agents.skill_mode import SkillSession


class FakeTracer:
    def log_tool_call(self, *args, **kwargs):
        return None

    def log_skill_mode_action(self, *args, **kwargs):
        return None

    def log_skill_mode_step(self, *args, **kwargs):
        return None

    def log_skill_mode_complete(self, *args, **kwargs):
        return None

    def get_events_for_ui(self, **kwargs):
        return []


def make_agent():
    agent = Agent.__new__(Agent)
    agent.model = None
    agent.tools = [{"function": {"name": "search"}}]
    return agent


async def run_replay_case(monkeypatch, *, responses, tool_output="lookup output", message="search issue", initial_session=None):
    from src.agents import core as core_mod

    call_counter = {"n": 0}
    snapshots = []

    async def fake_responses(**kwargs):
        idx = call_counter["n"]
        call_counter["n"] += 1
        return responses[min(idx, len(responses) - 1)]

    async def fake_execute_tool_by_name(name, **kwargs):
        if callable(tool_output):
            return tool_output(call_counter["n"])
        return tool_output

    async def fake_set_active(session_id, state):
        snapshots.append(state)

    async def fake_add_message(*args, **kwargs):
        snapshots.append({"_message_extra": kwargs.get("extra")})
        return "m1"

    class FakeSessionManager:
        set_active_skill_session = staticmethod(fake_set_active)
        add_message = staticmethod(fake_add_message)

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    monkeypatch.setattr(core_mod, "execute_tool_by_name", fake_execute_tool_by_name)
    monkeypatch.setattr(core_mod, "session_manager", FakeSessionManager)
    monkeypatch.setattr("src.skills.get_tracer", lambda: FakeTracer())

    skill = SimpleNamespace(name="lookup", description="search issue", path="", tools=[], strategy=[])
    agent = make_agent()

    skill_session = initial_session or SkillSession(skill_name="lookup", original_user_request=message)
    result = await agent._continue_skill_mode(
        message=message,
        session_id="s-replay",
        user_message_id="u-replay",
        skill_state=skill_session.to_dict(),
        skill=skill,
    )
    return result, snapshots, call_counter["n"]


def terminal_reasons(snapshots):
    return [s.get("termination_reason") for s in snapshots if isinstance(s, dict) and s.get("termination_reason")]


def latest_state(snapshots):
    states = [s for s in snapshots if isinstance(s, dict) and "status" in s]
    return states[-1] if states else {}


def terminal_snapshot_from_message(snapshots):
    for item in reversed(snapshots):
        if isinstance(item, dict) and item.get("_message_extra", {}).get("terminal_skill_session"):
            return item["_message_extra"]["terminal_skill_session"]
    return {}


@pytest.mark.asyncio
async def test_readonly_two_step_lookup_then_finish(monkeypatch):
    responses = [
        {"content": "", "function_calls": [{"id": "c1", "function": {"name": "search", "arguments": '{"q":"a"}'}}], "usage": {}},
        {"content": "", "function_calls": [{"id": "c2", "function": {"name": "search", "arguments": '{"q":"b"}'}}], "usage": {}},
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\ncomplete lookup", "function_calls": [], "usage": {}},
    ]
    _, snapshots, _ = await run_replay_case(monkeypatch, responses=responses, message="search issue details")
    assert terminal_reasons(snapshots)[-1] == "lookup_complete"


@pytest.mark.asyncio
async def test_readonly_generate_intent_not_early_finalize(monkeypatch):
    responses = [
        {"content": "", "function_calls": [{"id": "c1", "function": {"name": "search", "arguments": '{"q":"a"}'}}], "usage": {}},
        {"content": "", "function_calls": [{"id": "c2", "function": {"name": "search", "arguments": '{"q":"b"}'}}], "usage": {}},
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\nproduced doc", "function_calls": [], "usage": {}},
    ]
    _, snapshots, calls = await run_replay_case(monkeypatch, responses=responses, message="search issue and produce a doc")
    assert calls >= 4
    assert terminal_reasons(snapshots)[-1] == "no_function_calls"


@pytest.mark.asyncio
async def test_repeated_same_tool_output_no_progress(monkeypatch):
    responses = [
        {"content": "", "function_calls": [{"id": "c1", "function": {"name": "search", "arguments": '{"q":"same"}'}}], "usage": {}},
        {"content": "", "function_calls": [{"id": "c2", "function": {"name": "search", "arguments": '{"q":"same"}'}}], "usage": {}},
        {"content": "", "function_calls": [{"id": "c3", "function": {"name": "search", "arguments": '{"q":"same"}'}}], "usage": {}},
        {"content": "[FINISH]\ndone", "function_calls": [], "usage": {}},
    ]
    _, snapshots, _ = await run_replay_case(monkeypatch, responses=responses, tool_output="same output")
    assert "no_progress" in terminal_reasons(snapshots)
    assert latest_state(snapshots).get("transition") == "no_progress"


@pytest.mark.asyncio
async def test_different_tools_same_state_delta_no_progress(monkeypatch):
    responses = [
        {"content": "", "function_calls": [{"id": "c1", "function": {"name": "search", "arguments": '{"q":"same"}'}}], "usage": {}},
        {"content": "", "function_calls": [{"id": "c2", "function": {"name": "query", "arguments": '{"q":"same"}'}}], "usage": {}},
        {"content": "", "function_calls": [{"id": "c3", "function": {"name": "fetch", "arguments": '{"q":"same"}'}}], "usage": {}},
        {"content": "[FINISH]\ndone", "function_calls": [], "usage": {}},
    ]
    _, snapshots, _ = await run_replay_case(monkeypatch, responses=responses, tool_output="same output")
    assert "no_progress" in terminal_reasons(snapshots)


@pytest.mark.asyncio
async def test_invalid_finalizer_marker_retry_then_fallback(monkeypatch):
    responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[EXECUTE] invalid for finalizer", "function_calls": [], "usage": {}},
        {"content": "", "function_calls": [], "usage": {}},
    ]
    result, snapshots, _ = await run_replay_case(monkeypatch, responses=responses)
    assert terminal_reasons(snapshots)[-1] == "no_function_calls"
    assert any(isinstance(s, dict) and s.get("finalizer_state") == "terminal_failed" for s in snapshots)
    assert latest_state(snapshots).get("status") == "finished"
    assert "Skill execution completed with fallback summary." in result["response"]


@pytest.mark.asyncio
async def test_max_llm_calls_guard(monkeypatch):
    responses = [{"content": "", "function_calls": [{"id": "c", "function": {"name": "search", "arguments": '{"q":"x"}'}}], "usage": {}}]
    seeded = SkillSession(skill_name="lookup", original_user_request="search issue", llm_call_count=9)
    _, snapshots, calls = await run_replay_case(
        monkeypatch,
        responses=responses,
        tool_output=lambda n: f"output-{n}",
        initial_session=seeded,
    )
    # Last allowed LLM response (10th call) is processed, then no extra LLM call is made.
    assert calls == 1
    assert "max_llm_calls" in terminal_reasons(snapshots)
    state = latest_state(snapshots)
    assert state.get("llm_call_count", 0) == 10
    assert state.get("tool_round_count", 0) == 1


@pytest.mark.asyncio
async def test_max_tool_rounds_guard(monkeypatch):
    responses = [{"content": "", "function_calls": [{"id": "c", "function": {"name": "search", "arguments": '{"q":"x"}'}}], "usage": {}}]
    _, snapshots, _ = await run_replay_case(
        monkeypatch,
        responses=responses,
        tool_output=lambda n: f"diff-output-{n}",
    )
    assert "max_tool_rounds" in terminal_reasons(snapshots)


@pytest.mark.asyncio
async def test_no_function_round_does_not_increment_tool_round_count(monkeypatch):
    responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\nno tools needed", "function_calls": [], "usage": {}},
    ]
    _, snapshots, _ = await run_replay_case(monkeypatch, responses=responses)
    assert latest_state(snapshots).get("tool_round_count") == 0


@pytest.mark.asyncio
async def test_ask_user_path(monkeypatch):
    responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[ASK_USER]\nPlease provide repository name.", "function_calls": [], "usage": {}},
    ]
    result, snapshots, _ = await run_replay_case(monkeypatch, responses=responses)
    assert "Please provide repository name" in result["response"]
    assert terminal_reasons(snapshots)[-1] == "ask_user"


@pytest.mark.asyncio
async def test_finalizer_attempts_reset_on_later_finalize_cycle(monkeypatch):
    # First cycle: consume full finalizer retry budget.
    first_responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[EXECUTE] invalid", "function_calls": [], "usage": {}},
        {"content": "", "function_calls": [], "usage": {}},
    ]
    _, first_snapshots, _ = await run_replay_case(monkeypatch, responses=first_responses)
    carried_session = SkillSession.from_dict(latest_state(first_snapshots))
    assert carried_session.finalizer_attempts == 2

    # Second cycle (same session): should again get fresh 2-attempt budget.
    second_responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[EXECUTE] invalid again", "function_calls": [], "usage": {}},
        {"content": "", "function_calls": [], "usage": {}},
    ]
    _, second_snapshots, _ = await run_replay_case(
        monkeypatch,
        responses=second_responses,
        initial_session=carried_session,
    )
    assert latest_state(second_snapshots).get("finalizer_attempts") == 2


@pytest.mark.asyncio
async def test_stale_no_progress_state_resets_on_new_turn(monkeypatch):
    stale = SkillSession(
        skill_name="lookup",
        original_user_request="old",
        no_progress_count=3,
        last_progress_signature="stale-sig",
        last_tool_name="search",
        last_tool_args_signature="old-args",
        last_tool_output_signature="old-out",
    )
    responses = [
        {"content": "", "function_calls": [{"id": "c1", "function": {"name": "search", "arguments": '{"q":"fresh"}'}}], "usage": {}},
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\nfresh turn done", "function_calls": [], "usage": {}},
    ]
    _, snapshots, _ = await run_replay_case(
        monkeypatch,
        responses=responses,
        initial_session=stale,
        tool_output="fresh output",
        message="fresh user turn",
    )
    assert terminal_reasons(snapshots)[-1] in {"no_function_calls", "lookup_complete"}


@pytest.mark.asyncio
async def test_terminal_snapshot_recoverable_after_finish_clear(monkeypatch):
    responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\ncompleted", "function_calls": [], "usage": {}},
    ]
    result, snapshots, _ = await run_replay_case(monkeypatch, responses=responses)
    assert result["response"] == "completed"
    snapshot = terminal_snapshot_from_message(snapshots)
    assert snapshot.get("status") == "finished"
    assert snapshot.get("termination_reason") in {"no_function_calls", "finalizer_succeeded"}


def test_lookup_only_heuristic_ignores_ambiguous_pr_substrings():
    skill = SimpleNamespace(name="general", description="help text")
    session = SkillSession(skill_name="general", original_user_request="improve docs")
    assert _is_lookup_only_skill(skill, session, "improve this flow") is False
    assert _is_lookup_only_skill(skill, session, "prepare release checklist") is False


def test_skill_session_from_dict_backward_compatible():
    old = {
        "skill_name": "demo",
        "original_user_request": "help",
        "status": "active",
        "goal": "g",
        "plan": [],
        "completed_steps": [],
        "memory_summary": "",
        "artifacts": {},
        "pending_question": None,
    }
    sess = SkillSession.from_dict(old)
    assert sess.tool_round_count == 0
    assert sess.finalizer_state == "idle"
    assert sess.termination_reason == ""
