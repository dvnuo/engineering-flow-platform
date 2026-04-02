import pytest
from types import SimpleNamespace



class FakeTracer:
    def start_execution(self, **kwargs):
        return "exec-1"

    def complete_execution(self, *args, **kwargs):
        return None

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


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    efp_dir = tmp_path / ".efp"
    efp_dir.mkdir(exist_ok=True)

    config_file = efp_dir / "config.yaml"
    if not config_file.exists():
        config_file.write_text("{}", encoding="utf-8")

    import src.config as config_mod

    config_mod.config.config_path = config_file
    config_mod.config._config = {}
    config_mod.config._last_modified = 0
    config_mod.config.load()

    return efp_dir


def _mk_session_manager(calls):
    class FakeSessionManager:
        async def add_message(self, session_id, role, content, extra=None):
            calls.append(
                {
                    "session_id": session_id,
                    "role": role,
                    "content": content,
                    "extra": extra,
                }
            )
            return f"m{len(calls)}"

        async def get_history(self, session_id):
            return []

        async def set_active_skill_session(self, session_id, state):
            return None

    return FakeSessionManager()


@pytest.mark.asyncio
async def test_process_normal_path_persists_user_and_assistant_author_metadata(monkeypatch):
    from src.agents.core import Agent
    from src.agents import core as core_mod

    calls = []
    monkeypatch.setattr(core_mod, "session_manager", _mk_session_manager(calls))

    async def fake_fastlane(*args, **kwargs):
        return None

    async def fake_responses(**kwargs):
        return {"content": "assistant reply", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.fastlane.process_fastlane_command", fake_fastlane)
    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))
    monkeypatch.setattr("src.skills.get_tracer", lambda: FakeTracer())
    monkeypatch.setattr("src.skills.skill_registry._initialized", True)
    monkeypatch.setattr("src.skills.skill_registry.match_skill", lambda message: [])

    agent = Agent(agent_id="agent-123", agent_name="Helper Bot")
    await agent.process(
        message="hello",
        session_id="s1",
        user_name="Runtime User",
        portal_user_id=None,
        portal_user_name=None,
    )

    user_call = next(c for c in calls if c["role"] == "user")
    assert user_call["extra"]["author_type"] == "human"
    assert user_call["extra"]["author_id"] == "Runtime User"
    assert user_call["extra"]["author_name"] == "Runtime User"
    assert user_call["extra"]["author_source"] == "runtime"

    assistant_call = next(c for c in calls if c["role"] == "assistant")
    assert assistant_call["extra"]["author_type"] == "agent"
    assert assistant_call["extra"]["author_id"] == "agent-123"
    assert assistant_call["extra"]["author_name"] == "Helper Bot"
    assert assistant_call["extra"]["author_source"] == "runtime"


@pytest.mark.asyncio
async def test_process_fastlane_path_persists_assistant_author_metadata(monkeypatch):
    from src.agents.core import Agent
    from src.agents import core as core_mod

    calls = []
    monkeypatch.setattr(core_mod, "session_manager", _mk_session_manager(calls))

    async def fake_fastlane(*args, **kwargs):
        return "fastlane response"

    monkeypatch.setattr("src.agents.fastlane.process_fastlane_command", fake_fastlane)

    agent = Agent(agent_id="agent-fast", agent_name="Fast Bot")
    await agent.process(
        message="/status",
        session_id="s-fast",
        user_name="Portal User",
        portal_user_id="p-1",
        portal_user_name="Portal User",
    )

    assistant_call = next(c for c in calls if c["role"] == "assistant")
    assert assistant_call["content"] == "fastlane response"
    assert assistant_call["extra"]["author_type"] == "agent"
    assert assistant_call["extra"]["author_id"] == "agent-fast"
    assert assistant_call["extra"]["author_name"] == "Fast Bot"
    assert assistant_call["extra"]["author_source"] == "runtime"


@pytest.mark.asyncio
async def test_skill_finish_merges_terminal_snapshot_with_author_metadata(monkeypatch):
    from src.agents.core import Agent
    from src.agents import core as core_mod
    from src.agents.skill_mode import SkillSession

    calls = []
    monkeypatch.setattr(core_mod, "session_manager", _mk_session_manager(calls))
    monkeypatch.setattr("src.skills.get_tracer", lambda: FakeTracer())

    resp_calls = {"n": 0}

    async def fake_responses(**kwargs):
        resp_calls["n"] += 1
        if resp_calls["n"] == 1:
            return {"content": "", "function_calls": [], "usage": {}}
        return {"content": "[FINISH]\nDone", "function_calls": [], "usage": {}}

    monkeypatch.setattr(core_mod, "llm_client", SimpleNamespace(responses=fake_responses))

    agent = Agent.__new__(Agent)
    agent.model = None
    agent.agent_id = "agent-skill"
    agent.agent_name = "Skill Bot"
    agent.tools = [{"function": {"name": "search"}}]

    skill = SimpleNamespace(name="lookup", description="lookup skill", path="", tools=[], strategy=[])
    skill_session = SkillSession(skill_name="lookup", original_user_request="help")

    await agent._continue_skill_mode(
        message="help",
        session_id="s-skill",
        user_message_id="u1",
        skill_state=skill_session.to_dict(),
        skill=skill,
    )

    assistant_calls = [c for c in calls if c["role"] == "assistant"]
    assert assistant_calls, "expected assistant save call"
    finish_call = assistant_calls[-1]
    extra = finish_call["extra"]

    assert extra["author_type"] == "agent"
    assert extra["author_id"] == "agent-skill"
    assert extra["author_name"] == "Skill Bot"
    assert extra["author_source"] == "runtime"
    assert "terminal_skill_session" in extra
    assert isinstance(extra["terminal_skill_session"], dict)
