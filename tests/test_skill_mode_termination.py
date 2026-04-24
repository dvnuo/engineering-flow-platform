import pytest
from types import SimpleNamespace

from src.agents.core import Agent, _is_lookup_only_skill
from src.agents.skill_mode import SkillSession


class FakeTracer:
    def log_tool_call(self, *args, **kwargs):
        return None

    def log_skill_mode_entry(self, *args, **kwargs):
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
    agent.agent_id = None
    return agent


def test_direct_skill_prompt_no_clear_switch_confirmation_bias():
    from src.skills.runtime import build_skill_prompt_blocks

    skill = SimpleNamespace(
        name="direct-skill",
        description="Direct skill",
        path="",
        tools=[],
        task_tools=[],
        strategy=[],
        body="Do the thing",
        execution_style="direct",
        planning_mode="auto",
        staging_mode="auto",
    )
    blocks = build_skill_prompt_blocks(skill)
    assert "ask whether to clear/switch" not in blocks.system_rules.lower()
    assert "without asking for switch permission" in blocks.system_rules
    assert "continue current skill or switch to the new request" not in blocks.system_rules


def test_direct_skill_prompt_always_ask_conflict_policy_has_no_switching_contradiction():
    from src.skills.runtime import build_skill_prompt_blocks

    skill = SimpleNamespace(
        name="direct-skill",
        description="Direct skill",
        path="",
        tools=[],
        task_tools=[],
        strategy=[],
        body="Do the thing",
        execution_style="direct",
        planning_mode="auto",
        staging_mode="auto",
        active_skill_conflict_policy="always_ask",
    )
    blocks = build_skill_prompt_blocks(skill)
    assert "continue current skill or switch to the new request" in blocks.system_rules
    assert "without asking for switch permission" not in blocks.system_rules
    assert "allow switching/leaving this skill instead of forcing confirmation" not in blocks.system_rules


@pytest.mark.asyncio
async def test_start_skill_mode_skips_initial_plan_when_not_explicit_or_complex(monkeypatch):
    from src.agents import core as core_mod

    captured = {}

    def fake_resolve(skill, message, *, request_estimated_tokens=None, prompt_budget_tokens=None):
        captured["request_estimated_tokens"] = request_estimated_tokens
        captured["prompt_budget_tokens"] = prompt_budget_tokens
        return {"plan_required": False, "execution_style": "direct", "ask_user_policy": "blocked_only"}

    async def fake_continue(self, **kwargs):
        return {"response": "ok", "usage": {}, "events": []}

    async def fake_set_active(*args, **kwargs):
        return None

    monkeypatch.setattr(core_mod, "resolve_skill_response_flow", fake_resolve)
    monkeypatch.setattr(core_mod, "estimate_llm_request_tokens", lambda **kwargs: 3000)
    monkeypatch.setattr(core_mod, "resolve_prompt_budget", lambda **kwargs: {"prompt_budget_tokens": 32000})
    monkeypatch.setattr(core_mod.Agent, "_continue_skill_mode", fake_continue)
    monkeypatch.setattr(core_mod.session_manager, "set_active_skill_session", fake_set_active)
    monkeypatch.setattr("src.skills.get_tracer", lambda: FakeTracer())

    agent = make_agent()
    skill = SimpleNamespace(name="lookup", description="search issue", path="")
    await agent._start_skill_mode("small request", "s1", "u1", skill)
    assert captured["request_estimated_tokens"] == 3000
    assert captured["prompt_budget_tokens"] == 32000


@pytest.mark.asyncio
async def test_start_skill_mode_complex_first_turn_can_trigger_initial_plan(monkeypatch):
    from src.agents import core as core_mod

    plan_calls = {"n": 0}

    async def fake_continue(self, **kwargs):
        return {"response": "ok", "usage": {}, "events": []}

    async def fake_set_active(*args, **kwargs):
        return None

    async def fake_generate_initial_plan(*args, **kwargs):
        plan_calls["n"] += 1
        return "goal", [{"id": "s1", "type": "execute", "title": "step"}], {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    monkeypatch.setattr(core_mod, "estimate_llm_request_tokens", lambda **kwargs: 30000)
    monkeypatch.setattr(core_mod, "resolve_prompt_budget", lambda **kwargs: {"prompt_budget_tokens": 32000})
    monkeypatch.setattr(core_mod.Agent, "_continue_skill_mode", fake_continue)
    monkeypatch.setattr(core_mod.session_manager, "set_active_skill_session", fake_set_active)
    monkeypatch.setattr(core_mod, "generate_initial_skill_plan", fake_generate_initial_plan)
    monkeypatch.setattr("src.skills.get_tracer", lambda: FakeTracer())

    agent = make_agent()
    skill = SimpleNamespace(
        name="lookup",
        description="search issue",
        path="",
        planning_mode="auto",
        staging_mode="auto",
        execution_style="direct",
        ask_user_policy="blocked_only",
    )
    await agent._start_skill_mode("big request", "s1", "u1", skill)
    assert plan_calls["n"] == 1

async def run_replay_case(
    monkeypatch,
    *,
    responses,
    tool_output="lookup output",
    message="search issue",
    initial_session=None,
    capture_llm_kwargs=None,
    tracer_factory=None,
    skill_name="lookup",
    skill_metadata=None,
):
    from src.agents import core as core_mod

    call_counter = {"n": 0}
    snapshots = []

    async def fake_responses(**kwargs):
        idx = call_counter["n"]
        call_counter["n"] += 1
        if isinstance(capture_llm_kwargs, list):
            capture_llm_kwargs.append(dict(kwargs))
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
    tracer_provider = tracer_factory or (lambda: FakeTracer())
    monkeypatch.setattr("src.skills.get_tracer", tracer_provider)

    metadata = {"planning_mode": "auto", "staging_mode": "auto", "execution_style": "direct", "ask_user_policy": "blocked_only"}
    if isinstance(skill_metadata, dict):
        metadata.update(skill_metadata)
    if skill_name == "mobilex-test-cases-generator":
        metadata["staging_mode"] = "required"
        metadata["execution_style"] = "stepwise"
        metadata["planning_mode"] = "required"
    skill = SimpleNamespace(name=skill_name, description="search issue", path="", tools=[], strategy=[], **metadata)
    agent = make_agent()

    skill_session = initial_session or SkillSession(skill_name=skill_name, original_user_request=message)
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
async def test_continue_skill_mode_logs_runtime_config_resolution_failure(monkeypatch, caplog):
    failing_registry = SimpleNamespace(
        get_skill=lambda *_: None,
        get_skill_runtime_config=lambda *_: (_ for _ in ()).throw(RuntimeError("config boom")),
    )
    monkeypatch.setattr("src.skills.skill_registry", failing_registry)

    responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[ASK_USER]\nPlease provide repository name.", "function_calls": [], "usage": {}},
    ]
    with caplog.at_level("DEBUG"):
        result, snapshots, _ = await run_replay_case(monkeypatch, responses=responses)

    assert "Please provide repository name" in result["response"]
    assert terminal_reasons(snapshots)[-1] == "ask_user"
    assert (
        "[SkillMode] Failed to resolve runtime config for skill lookup; continuing without runtime config"
        in caplog.text
    )


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


@pytest.mark.asyncio
async def test_run_skill_finalizer_uses_passed_max_tokens(monkeypatch):
    from src.agents import core as core_mod

    captured = {}
    original_max = core_mod.config.llm.get("max_tokens")
    original_allow_lower = core_mod.config.llm.get("allow_lower_max_tokens_than_model_limit")
    core_mod.config.llm["max_tokens"] = 64000
    core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = True

    async def fake_responses(**kwargs):
        captured["max_tokens"] = kwargs.get("max_tokens")
        return {"content": "[FINISH]\ndone", "usage": {}}

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))

    try:
        result, _usage = await core_mod._run_skill_finalizer(
            input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            system_prompt="sys",
            provider="openai",
            model="gpt-5-mini",
            skill_session=SkillSession(skill_name="lookup", original_user_request="x"),
            track_usage=False,
            usage_data={},
            remaining_llm_budget=1,
            max_tokens=4096,
        )
    finally:
        if original_max is None:
            core_mod.config.llm.pop("max_tokens", None)
        else:
            core_mod.config.llm["max_tokens"] = original_max
        if original_allow_lower is None:
            core_mod.config.llm.pop("allow_lower_max_tokens_than_model_limit", None)
        else:
            core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = original_allow_lower

    assert result.state == "succeeded"
    assert captured["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_initial_skill_plan_direct_uses_model_derived_max_tokens(monkeypatch):
    from src.agents import skill_mode

    captured = {}
    original_max = skill_mode.config.llm.get("max_tokens")
    original_allow_lower = skill_mode.config.llm.get("allow_lower_max_tokens_than_model_limit")
    original_model = skill_mode.config.llm.get("model")
    skill_mode.config.llm["model"] = "gpt-5.4-mini"
    skill_mode.config.llm["max_tokens"] = 64000
    skill_mode.config.llm["allow_lower_max_tokens_than_model_limit"] = False

    async def _fake_responses(**kwargs):
        captured["max_tokens"] = kwargs.get("max_tokens")
        return {"content": "{\"goal\":\"g\",\"steps\":[]}", "tool_calls": [], "function_calls": [], "usage": {}}

    monkeypatch.setattr(skill_mode, "llm_client", SimpleNamespace(responses=_fake_responses))
    try:
        result = await skill_mode._generate_initial_skill_plan_direct(
            skill=SimpleNamespace(name="lookup", description="desc", strategy=[], tools=[]),
            user_message="plan this",
            model="gpt-5.4-mini",
        )
    finally:
        if original_max is None:
            skill_mode.config.llm.pop("max_tokens", None)
        else:
            skill_mode.config.llm["max_tokens"] = original_max
        if original_allow_lower is None:
            skill_mode.config.llm.pop("allow_lower_max_tokens_than_model_limit", None)
        else:
            skill_mode.config.llm["allow_lower_max_tokens_than_model_limit"] = original_allow_lower
        if original_model is None:
            skill_mode.config.llm.pop("model", None)
        else:
            skill_mode.config.llm["model"] = original_model

    assert result.get("goal") == "g"
    assert captured.get("max_tokens") == 128000


@pytest.mark.asyncio
async def test_run_skill_finalizer_omitted_max_tokens_uses_model_limit(monkeypatch):
    from src.agents import core as core_mod

    captured = {}
    original_max = core_mod.config.llm.get("max_tokens")
    original_allow_lower = core_mod.config.llm.get("allow_lower_max_tokens_than_model_limit")
    core_mod.config.llm["max_tokens"] = 64000
    core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = False

    async def fake_responses(**kwargs):
        captured["max_tokens"] = kwargs.get("max_tokens")
        return {"content": "[FINISH]\ndone", "usage": {}}

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    try:
        result, _usage = await core_mod._run_skill_finalizer(
            input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            system_prompt="sys",
            provider="openai",
            model="gpt-5.4-mini",
            skill_session=SkillSession(skill_name="lookup", original_user_request="x"),
            track_usage=False,
            usage_data={},
            remaining_llm_budget=1,
            max_tokens=None,
        )
    finally:
        if original_max is None:
            core_mod.config.llm.pop("max_tokens", None)
        else:
            core_mod.config.llm["max_tokens"] = original_max
        if original_allow_lower is None:
            core_mod.config.llm.pop("allow_lower_max_tokens_than_model_limit", None)
        else:
            core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = original_allow_lower

    assert result.state == "succeeded"
    assert captured["max_tokens"] == 128000
    assert result.request_budget.get("legacy_max_tokens_ignored") is True


@pytest.mark.asyncio
async def test_run_skill_finalizer_ignores_legacy_lower_config_cap_by_default(monkeypatch):
    from src.agents import core as core_mod

    captured = {}
    original_max = core_mod.config.llm.get("max_tokens")
    original_allow_lower = core_mod.config.llm.get("allow_lower_max_tokens_than_model_limit")
    core_mod.config.llm["max_tokens"] = 2048
    core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = False

    async def fake_responses(**kwargs):
        captured["max_tokens"] = kwargs.get("max_tokens")
        return {"content": "[FINISH]\ndone", "usage": {}}

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    try:
        result, _usage = await core_mod._run_skill_finalizer(
            input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            system_prompt="sys",
            provider="openai",
            model="gpt-5-mini",
            skill_session=SkillSession(skill_name="lookup", original_user_request="x"),
            track_usage=False,
            usage_data={},
            remaining_llm_budget=1,
            max_tokens=4096,
        )
    finally:
        if original_max is None:
            core_mod.config.llm.pop("max_tokens", None)
        else:
            core_mod.config.llm["max_tokens"] = original_max
        if original_allow_lower is None:
            core_mod.config.llm.pop("allow_lower_max_tokens_than_model_limit", None)
        else:
            core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = original_allow_lower

    assert result.state == "succeeded"
    assert captured["max_tokens"] == 64000


@pytest.mark.asyncio
async def test_run_skill_finalizer_honors_explicit_lower_config_cap(monkeypatch):
    from src.agents import core as core_mod

    captured = {}
    original_max = core_mod.config.llm.get("max_tokens")
    original_allow_lower = core_mod.config.llm.get("allow_lower_max_tokens_than_model_limit")
    core_mod.config.llm["max_tokens"] = 2048
    core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = True

    async def fake_responses(**kwargs):
        captured["max_tokens"] = kwargs.get("max_tokens")
        return {"content": "[FINISH]\ndone", "usage": {}}

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    try:
        result, _usage = await core_mod._run_skill_finalizer(
            input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            system_prompt="sys",
            provider="openai",
            model="gpt-5-mini",
            skill_session=SkillSession(skill_name="lookup", original_user_request="x"),
            track_usage=False,
            usage_data={},
            remaining_llm_budget=1,
            max_tokens=4096,
        )
    finally:
        if original_max is None:
            core_mod.config.llm.pop("max_tokens", None)
        else:
            core_mod.config.llm["max_tokens"] = original_max
        if original_allow_lower is None:
            core_mod.config.llm.pop("allow_lower_max_tokens_than_model_limit", None)
        else:
            core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = original_allow_lower

    assert result.state == "succeeded"
    assert captured["max_tokens"] == 2048
    assert result.request_budget.get("legacy_max_tokens_ignored") is False


@pytest.mark.asyncio
async def test_run_skill_finalizer_aborts_when_request_over_budget(monkeypatch):
    from src.agents import core as core_mod

    calls = {"llm": 0}

    async def fake_responses(**kwargs):
        calls["llm"] += 1
        return {"content": "[FINISH]\nshould-not-happen", "usage": {}}

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    monkeypatch.setattr(
        core_mod,
        "estimate_llm_request_tokens",
        lambda **kwargs: 50000 if not kwargs.get("tools") else 100,
    )
    monkeypatch.setattr(
        core_mod,
        "resolve_prompt_budget",
        lambda **kwargs: {
            # Synthetic low-budget fixture: explicitly legacy/over-budget path test.
            "prompt_budget_tokens": 28000,
            "max_output_tokens": 4096,
            "reserved_output_tokens": 1000,
            "safety_margin_tokens": 500,
            "max_prompt_tokens": 28000,
        },
    )

    finalizer_result, _usage = await core_mod._run_skill_finalizer(
        input_items=[{"type": "function_call_output", "call_id": "c1", "output": "[large source tool result projected]\ncontext_ref: ctx://context/s/k/aaaaaaaaaaaa\n" + ("X" * 6000)}],
        system_prompt="sys",
        provider="openai",
        model="gpt-5-mini",
        skill_session=SkillSession(skill_name="lookup", original_user_request="x"),
        track_usage=False,
        usage_data={},
        remaining_llm_budget=1,
        max_tokens=4096,
    )

    assert calls["llm"] == 0
    assert finalizer_result.state == "terminal_failed"
    assert finalizer_result.termination_reason == "finalizer_context_budget_exceeded"
    assert finalizer_result.request_budget.get("request_over_budget") is True
    assert finalizer_result.request_budget.get("stage") == "skill_finalizer"


@pytest.mark.asyncio
async def test_continue_skill_mode_uses_budget_max_output_tokens_for_llm_calls(monkeypatch):
    from src.agents import core as core_mod

    captured = []
    original_max = core_mod.config.llm.get("max_tokens")
    original_allow_lower = core_mod.config.llm.get("allow_lower_max_tokens_than_model_limit")
    original_model = core_mod.config.llm.get("model")
    core_mod.config.llm["model"] = "gpt-5-mini"
    core_mod.config.llm["max_tokens"] = 4096
    core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = True

    monkeypatch.setattr(
        core_mod,
        "resolve_prompt_budget",
        lambda **kwargs: {
            "prompt_budget_tokens": 50000,
            "max_output_tokens": 4096,
            "reserved_output_tokens": 2000,
            "safety_margin_tokens": 500,
            "max_prompt_tokens": 50000,
        },
    )
    responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\ndone", "function_calls": [], "usage": {}},
    ]
    try:
        await run_replay_case(monkeypatch, responses=responses, capture_llm_kwargs=captured)
    finally:
        if original_max is None:
            core_mod.config.llm.pop("max_tokens", None)
        else:
            core_mod.config.llm["max_tokens"] = original_max
        if original_allow_lower is None:
            core_mod.config.llm.pop("allow_lower_max_tokens_than_model_limit", None)
        else:
            core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = original_allow_lower
        if original_model is None:
            core_mod.config.llm.pop("model", None)
        else:
            core_mod.config.llm["model"] = original_model

    assert captured
    assert captured[0].get("max_tokens") == 4096


@pytest.mark.asyncio
async def test_continue_skill_mode_uses_model_max_output_tokens_by_default_for_gpt_5_4_mini(monkeypatch):
    from src.agents import core as core_mod

    captured = []
    original_max = core_mod.config.llm.get("max_tokens")
    original_allow_lower = core_mod.config.llm.get("allow_lower_max_tokens_than_model_limit")
    original_model = core_mod.config.llm.get("model")
    core_mod.config.llm["model"] = "gpt-5.4-mini"
    core_mod.config.llm["max_tokens"] = 64000
    core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = False

    monkeypatch.setattr(
        core_mod,
        "resolve_prompt_budget",
        lambda **kwargs: {
            "prompt_budget_tokens": 50000,
            "max_output_tokens": 128000,
            "reserved_output_tokens": 128000,
            "safety_margin_tokens": 8000,
            "max_prompt_tokens": 272000,
        },
    )
    responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\ndone", "function_calls": [], "usage": {}},
    ]
    try:
        await run_replay_case(
            monkeypatch,
            responses=responses,
            capture_llm_kwargs=captured,
            message="search issue details",
        )
    finally:
        if original_max is None:
            core_mod.config.llm.pop("max_tokens", None)
        else:
            core_mod.config.llm["max_tokens"] = original_max
        if original_allow_lower is None:
            core_mod.config.llm.pop("allow_lower_max_tokens_than_model_limit", None)
        else:
            core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = original_allow_lower
        if original_model is None:
            core_mod.config.llm.pop("model", None)
        else:
            core_mod.config.llm["model"] = original_model

    assert captured
    assert captured[0].get("max_tokens") in (64000, 128000)


@pytest.mark.asyncio
async def test_continue_skill_mode_honors_explicit_lower_max_tokens_override(monkeypatch):
    from src.agents import core as core_mod

    captured = []
    original_max = core_mod.config.llm.get("max_tokens")
    original_allow_lower = core_mod.config.llm.get("allow_lower_max_tokens_than_model_limit")
    original_model = core_mod.config.llm.get("model")
    core_mod.config.llm["model"] = "gpt-5.4-mini"
    core_mod.config.llm["max_tokens"] = 64000
    core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = True

    monkeypatch.setattr(
        core_mod,
        "resolve_prompt_budget",
        lambda **kwargs: {
            "prompt_budget_tokens": 50000,
            "max_output_tokens": 128000,
            "reserved_output_tokens": 128000,
            "safety_margin_tokens": 8000,
            "max_prompt_tokens": 272000,
        },
    )
    responses = [
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\ndone", "function_calls": [], "usage": {}},
    ]
    try:
        await run_replay_case(
            monkeypatch,
            responses=responses,
            capture_llm_kwargs=captured,
            message="search issue details",
        )
    finally:
        if original_max is None:
            core_mod.config.llm.pop("max_tokens", None)
        else:
            core_mod.config.llm["max_tokens"] = original_max
        if original_allow_lower is None:
            core_mod.config.llm.pop("allow_lower_max_tokens_than_model_limit", None)
        else:
            core_mod.config.llm["allow_lower_max_tokens_than_model_limit"] = original_allow_lower
        if original_model is None:
            core_mod.config.llm.pop("model", None)
        else:
            core_mod.config.llm["model"] = original_model

    assert captured
    assert captured[0].get("max_tokens") == 64000


@pytest.mark.asyncio
async def test_continue_skill_mode_finalizer_abort_includes_finalizer_request_budget(monkeypatch):
    from src.agents import core as core_mod

    async def fake_responses(**kwargs):
        return {"content": "", "function_calls": [], "usage": {}}

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    monkeypatch.setattr(
        core_mod,
        "estimate_llm_request_tokens",
        lambda **kwargs: 50000 if not kwargs.get("tools") else 100,
    )
    monkeypatch.setattr(
        core_mod,
        "resolve_prompt_budget",
        lambda **kwargs: {
            # Synthetic low-budget fixture for explicit over-budget/finalizer abort behavior.
            "prompt_budget_tokens": 28000,
            "max_output_tokens": 4096,
            "reserved_output_tokens": 1000,
            "safety_margin_tokens": 500,
            "max_prompt_tokens": 28000,
        },
    )

    result, _snapshots, _calls = await run_replay_case(
        monkeypatch,
        responses=[{"content": "", "function_calls": [], "usage": {}}],
    )
    assert isinstance(result.get("request_budget"), dict)
    assert result["request_budget"].get("stage") == "skill_finalizer"
    assert result["request_budget"].get("request_budget_stage") == "skill_finalizer"
    assert result["request_budget"].get("request_over_budget") is True


@pytest.mark.asyncio
async def test_continue_skill_mode_hard_guard_denies_out_of_policy_tool(monkeypatch):
    from src.agents import core as core_mod
    from src.skills.runtime import SkillRuntimeConfig

    responses = [
        {"content": "", "function_calls": [{"id": "c1", "function": {"name": "github_push", "arguments": "{}"}}], "usage": {}},
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\ndone", "function_calls": [], "usage": {}},
    ]
    runtime_cfg = SkillRuntimeConfig(
        skill_name="lookup",
        allowed_tools=["jira_get_issue_by_url"],
        allowed_tools_set={"jira_get_issue_by_url"},
        tool_policy_declared=True,
    )
    monkeypatch.setattr("src.skills.skill_registry.get_skill_runtime_config", lambda *args, **kwargs: runtime_cfg)
    tracer_calls = []

    class CaptureTracer(FakeTracer):
        def log_tool_call(self, *args, **kwargs):
            tracer_calls.append({"args": args, "kwargs": kwargs})
            return None

    async def _forbidden_execute(*args, **kwargs):
        raise AssertionError("out-of-policy tool execution should not be called")

    monkeypatch.setattr(core_mod, "_execute_tool_via_runtime_bus", _forbidden_execute)
    captured_llm_kwargs = []

    result, _snapshots, _calls = await run_replay_case(
        monkeypatch,
        responses=responses,
        capture_llm_kwargs=captured_llm_kwargs,
        tracer_factory=lambda: CaptureTracer(),
    )
    assert "done" in result["response"] or "fallback" in result["response"].lower()
    assert tracer_calls
    denied = tracer_calls[-1]["kwargs"]
    assert denied.get("success") is False
    assert denied.get("error") == "denied_by_skill_policy"
    assert len(captured_llm_kwargs) >= 2
    feedback_items = [
        item
        for item in captured_llm_kwargs[1].get("input_items", [])
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert any(item.get("call_id") == "c1" for item in feedback_items)


@pytest.mark.asyncio
async def test_continue_skill_mode_hard_guard_allows_context_read_ref(monkeypatch):
    from src.agents import core as core_mod
    from src.skills.runtime import SkillRuntimeConfig

    responses = [
        {"content": "", "function_calls": [{"id": "c1", "function": {"name": "context_read_ref", "arguments": "{\"ref\":\"ctx://context/s/k/aaaaaaaaaaaa\"}"}}], "usage": {}},
        {"content": "", "function_calls": [], "usage": {}},
        {"content": "[FINISH]\ndone", "function_calls": [], "usage": {}},
    ]
    runtime_cfg = SkillRuntimeConfig(
        skill_name="lookup",
        allowed_tools=["jira_get_issue_by_url"],
        allowed_tools_set={"jira_get_issue_by_url"},
        tool_policy_declared=True,
    )
    monkeypatch.setattr("src.skills.skill_registry.get_skill_runtime_config", lambda *args, **kwargs: runtime_cfg)
    called = {"tool": None}

    async def _fake_execute(*, tool_name, **kwargs):
        called["tool"] = tool_name
        return core_mod.ToolResult(success=True, content="ok", error=None)

    monkeypatch.setattr(core_mod, "_execute_tool_via_runtime_bus", _fake_execute)

    result, _snapshots, _calls = await run_replay_case(monkeypatch, responses=responses)
    assert called["tool"] == "context_read_ref"
    assert "done" in result["response"] or "fallback" in result["response"].lower()


@pytest.mark.asyncio
async def test_continue_skill_mode_error_response_includes_skill_generation_budget_stage(monkeypatch):
    from src.agents import core as core_mod
    monkeypatch.setattr(
        core_mod,
        "resolve_prompt_budget",
        lambda **kwargs: {
            "prompt_budget_tokens": 50000,
            "max_output_tokens": 4096,
            "reserved_output_tokens": 1000,
            "safety_margin_tokens": 500,
            "max_prompt_tokens": 50000,
        },
    )

    result, _snapshots, _calls = await run_replay_case(
        monkeypatch,
        responses=[{"error": {"message": "boom", "type": "llm_error"}}],
    )
    assert result["request_budget"].get("request_budget_stage") == "skill_generation"
    assert result["request_budget"].get("large_generation_guard_applied") is False
    assert result["request_budget"].get("output_size_guard_applied") is False


@pytest.mark.asyncio
async def test_continue_skill_mode_over_budget_error_includes_skill_generation_stage_fields(monkeypatch):
    from src.agents import core as core_mod

    monkeypatch.setattr(
        core_mod,
        "estimate_llm_request_tokens",
        # Synthetic over-budget request size fixture; not a model-limit expectation.
        lambda **kwargs: 60000,
    )
    monkeypatch.setattr(
        core_mod,
        "resolve_prompt_budget",
        lambda **kwargs: {
            # Synthetic low-budget fixture for context-budget-exceeded path testing.
            "prompt_budget_tokens": 28000,
            "max_output_tokens": 4096,
            "reserved_output_tokens": 1000,
            "safety_margin_tokens": 500,
            "max_prompt_tokens": 28000,
        },
    )

    result, _snapshots, calls = await run_replay_case(
        monkeypatch,
        responses=[{"content": "", "function_calls": [], "usage": {}}],
    )

    assert calls == 0
    assert result["error_type"] == "context_budget_exceeded"
    assert result["details"]["request_budget_stage"] == "skill_generation"
    assert result["details"]["stage"] == "skill_generation"
    assert result["request_budget"]["request_budget_stage"] == "skill_generation"


@pytest.mark.asyncio
async def test_continue_skill_mode_mobilex_budget_marks_large_generation_guard_applied(monkeypatch):
    result, _snapshots, _calls = await run_replay_case(
        monkeypatch,
        responses=[{"error": {"message": "boom", "type": "llm_error"}}],
        skill_name="mobilex-test-cases-generator",
    )

    assert result["request_budget"].get("request_budget_stage") == "skill_generation"
    assert result["request_budget"].get("large_generation_guard_applied") is True
    assert result["request_budget"].get("output_size_guard_applied") is True
    assert "skill_metadata" in str(result["request_budget"].get("large_generation_guard_reason") or "")


@pytest.mark.asyncio
async def test_start_skill_mode_direct_skill_skips_initial_plan(monkeypatch):
    from src.agents import core as core_mod

    plan_called = {"v": False}

    async def fake_plan(*args, **kwargs):
        plan_called["v"] = True
        return "x", [{"id": "s1", "type": "execute", "title": "x"}], {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    monkeypatch.setattr(core_mod, "generate_initial_skill_plan", fake_plan)
    async def _fake_set_active(*args, **kwargs):
        return None

    monkeypatch.setattr(core_mod.session_manager, "set_active_skill_session", _fake_set_active)

    async def _fake_continue(**kwargs):
        return {"response": "ok", "usage": {}}

    agent = make_agent()
    agent._continue_skill_mode = _fake_continue
    skill = SimpleNamespace(name="create-pull-request", description="create pr", path="", planning_mode="auto", staging_mode="auto", execution_style="direct", ask_user_policy="blocked_only")
    await agent._start_skill_mode("create a PR", "s", "u", skill)
    assert plan_called["v"] is False


def test_skill_mode_prompt_direct_does_not_force_one_small_step():
    from src.agents.skill_mode import _build_skill_mode_system_prompt

    skill = SimpleNamespace(name="direct", description="desc", path="", strategy=[])
    prompt = _build_skill_mode_system_prompt(skill, SkillSession(skill_name="direct", original_user_request="x"), execution_style="direct")
    assert "Advance only ONE small step this turn" not in prompt


def test_skill_mode_prompt_stepwise_keeps_one_small_step_rule():
    from src.agents.skill_mode import _build_skill_mode_system_prompt

    skill = SimpleNamespace(name="stepwise", description="desc", path="", strategy=[])
    prompt = _build_skill_mode_system_prompt(skill, SkillSession(skill_name="stepwise", original_user_request="x"), execution_style="stepwise")
    assert "Advance only ONE small step this turn" in prompt
