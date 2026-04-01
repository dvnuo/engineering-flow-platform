import pytest
from types import SimpleNamespace

from src.agents.core import Agent, _build_progress_signature, _decide_skill_loop_transition, _is_lookup_only_skill
from src.agents.skill_mode import SkillSession


class _FakeTracer:
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


def _make_agent():
    agent = Agent.__new__(Agent)
    agent.model = None
    agent.tools = [{"function": {"name": "search"}}]
    return agent


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


def test_progress_signature_unchanged_for_same_round_data():
    sess = SkillSession(skill_name="x", original_user_request="y")
    sess.completed_steps.append({"type": "tool_result", "result": "same"})
    sig1 = _build_progress_signature(sess, "search", {"q": "abc"}, "output")
    sig2 = _build_progress_signature(sess, "search", {"q": "abc"}, "output")
    assert sig1["progress_signature"] == sig2["progress_signature"]


def test_lookup_only_heuristic_respects_generate_intent():
    skill = SimpleNamespace(name="issue-helper", description="get issue info")
    sess = SkillSession(skill_name="issue-helper", original_user_request="")
    assert _is_lookup_only_skill(skill, sess, "get issue details") is True
    assert _is_lookup_only_skill(skill, sess, "get issue details and produce a doc") is False


def test_decider_no_progress_triggers_finalizer():
    decision = _decide_skill_loop_transition(
        round_num=1,
        max_rounds=6,
        has_function_calls=True,
        no_progress_count=2,
        has_readonly_tool_success=False,
        has_write_tool_call=False,
        lookup_only_skill=False,
    )
    assert decision["run_finalizer"] is True
    assert decision["reason"] == "no_progress"


@pytest.mark.asyncio
async def test_finalizer_retry_then_fallback_sets_terminal_reason(monkeypatch):
    from src.agents import core as core_mod

    calls = {"n": 0}

    async def fake_responses(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "", "function_calls": [], "usage": {}}
        return {"error": {"message": "boom", "type": "llm_error"}, "usage": {}}

    saved_states = []

    async def fake_set_active(session_id, state):
        saved_states.append(state)

    async def fake_add_message(*args, **kwargs):
        return "m1"

    class _FakeSessionManager:
        set_active_skill_session = staticmethod(fake_set_active)
        add_message = staticmethod(fake_add_message)

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    monkeypatch.setattr(core_mod, "session_manager", _FakeSessionManager)
    monkeypatch.setattr("src.skills.get_tracer", lambda: _FakeTracer())

    skill = SimpleNamespace(name="lookup", description="get info", path="", tools=[], strategy=[])
    agent = _make_agent()

    result = await agent._continue_skill_mode(
        message="get issue info",
        session_id="s1",
        user_message_id="u1",
        skill_state=SkillSession(skill_name="lookup", original_user_request="get issue info").to_dict(),
        skill=skill,
    )

    assert "response" in result
    assert calls["n"] == 3  # 1 normal + 2 finalizer attempts
    # final set call before return should include terminal reason
    assert any(state and state.get("termination_reason") for state in saved_states)
    assert any(state and state.get("finalizer_attempts", 0) >= 2 for state in saved_states)


@pytest.mark.asyncio
async def test_no_progress_repeated_same_tool_output_terminates(monkeypatch):
    from src.agents import core as core_mod

    calls = {"n": 0}

    async def fake_responses(**kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return {
                "content": "",
                "function_calls": [{"id": f"c{calls['n']}", "function": {"name": "search", "arguments": '{"q":"abc"}'}}],
                "usage": {},
            }
        return {"content": "[FINISH] done", "function_calls": [], "usage": {}}

    async def fake_execute_tool_by_name(name, **kwargs):
        return "same output"

    saved_states = []

    async def fake_set_active(session_id, state):
        saved_states.append(state)

    async def fake_add_message(*args, **kwargs):
        return "m1"

    class _FakeSessionManager:
        set_active_skill_session = staticmethod(fake_set_active)
        add_message = staticmethod(fake_add_message)

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    monkeypatch.setattr(core_mod, "execute_tool_by_name", fake_execute_tool_by_name)
    monkeypatch.setattr(core_mod, "session_manager", _FakeSessionManager)
    monkeypatch.setattr("src.skills.get_tracer", lambda: _FakeTracer())

    skill = SimpleNamespace(name="lookup", description="search issue", path="", tools=[], strategy=[])
    agent = _make_agent()

    await agent._continue_skill_mode(
        message="search issue",
        session_id="s2",
        user_message_id="u2",
        skill_state=SkillSession(skill_name="lookup", original_user_request="search issue").to_dict(),
        skill=skill,
    )

    assert any(state and state.get("termination_reason") for state in saved_states)


@pytest.mark.asyncio
async def test_readonly_lookup_not_auto_finish_when_generate_requested(monkeypatch):
    from src.agents import core as core_mod

    calls = {"n": 0}

    async def fake_responses(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "function_calls": [{"id": "c1", "function": {"name": "search", "arguments": '{"q":"abc"}'}}],
                "usage": {},
            }
        if calls["n"] == 2:
            # proves tool loop continued instead of readonly immediate finalize
            return {
                "content": "",
                "function_calls": [{"id": "c2", "function": {"name": "search", "arguments": '{"q":"def"}'}}],
                "usage": {},
            }
        return {"content": "[FINISH] produced doc", "function_calls": [], "usage": {}}

    async def fake_execute_tool_by_name(name, **kwargs):
        return "lookup output"

    async def fake_set_active(*args, **kwargs):
        return None

    async def fake_add_message(*args, **kwargs):
        return "m1"

    class _FakeSessionManager:
        set_active_skill_session = staticmethod(fake_set_active)
        add_message = staticmethod(fake_add_message)

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    monkeypatch.setattr(core_mod, "execute_tool_by_name", fake_execute_tool_by_name)
    monkeypatch.setattr(core_mod, "session_manager", _FakeSessionManager)
    monkeypatch.setattr("src.skills.get_tracer", lambda: _FakeTracer())

    skill = SimpleNamespace(name="lookup", description="search issue", path="", tools=[], strategy=[])
    agent = _make_agent()

    await agent._continue_skill_mode(
        message="search and produce a doc",
        session_id="s3",
        user_message_id="u3",
        skill_state=SkillSession(skill_name="lookup", original_user_request="search and produce a doc").to_dict(),
        skill=skill,
    )

    assert calls["n"] >= 3
