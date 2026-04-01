import pytest
from types import SimpleNamespace

from src.agents.core import Agent
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
    _, snapshots, _ = await run_replay_case(monkeypatch, responses=responses)
    assert terminal_reasons(snapshots)[-1] == "no_function_calls"
    assert any(isinstance(s, dict) and s.get("finalizer_state") == "terminal_failed" for s in snapshots)


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
    assert calls >= 2
    assert "max_llm_calls" in terminal_reasons(snapshots)


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
async def test_ask_user_path(monkeypatch):
    responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[ASK_USER]\nPlease provide repository name.", "function_calls": [], "usage": {}},
    ]
    result, snapshots, _ = await run_replay_case(monkeypatch, responses=responses)
    assert "Please provide repository name" in result["response"]
    assert terminal_reasons(snapshots)[-1] == "ask_user"


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
