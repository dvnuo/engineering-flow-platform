import pytest
from types import SimpleNamespace
from pathlib import Path

from src import ToolResult
from src.agents.core import Agent, get_skill_workdir, set_skill_workdir, _execute_tool_via_runtime_bus
from src.agents.skill_runtime import build_skill_tool_denied_result
from src.agents.skill_runtime import (
    build_skill_runtime_event_payload,
    dispatch_skill_hook,
    resolve_prompt_execution_boundary,
)
from src.agents.tasks import TaskManager, TaskRecord
from src.skills.registry import SkillRegistry
from src.skills.registry import Skill
from src.skills.runtime import (
    attach_skill_references,
    assemble_effective_prompt,
    build_skill_runtime_config,
    compile_skill_prompt_contract,
    summarize_skill_references,
)
from src.agents.skill_mode import generate_initial_skill_plan
from src.runtime.contracts import make_execution_result

REPO_ROOT = Path(__file__).resolve().parents[1]
CREATE_PULL_REQUEST_SKILL_PATH = REPO_ROOT / "skills" / "create-pull-request" / "skill.md"


class _Tracer:
    def start_execution(self, **kwargs):
        return "exec-1"

    def log_tool_call(self, *args, **kwargs):
        return None

    def complete_execution(self, *args, **kwargs):
        return None

    def get_events_for_ui(self, **kwargs):
        return []

    def log_thinking(self, *args, **kwargs):
        return None


class _SessionManager:
    def __init__(self):
        self.messages = []
        self.active_skill_session = None
        self.active_skill_sessions = {}

    async def add_message(self, session_id, role, content, extra=None, wait_for_save=False):
        self.messages.append({"role": role, "content": content, **(extra or {})})
        return f"msg-{len(self.messages)}"

    async def get_history(self, session_id):
        return list(self.messages)

    async def get_active_skill_session(self, session_id):
        if session_id in self.active_skill_sessions:
            return self.active_skill_sessions.get(session_id)
        if not self.active_skill_sessions:
            return self.active_skill_session
        return None

    async def set_active_skill_session(self, session_id, skill_session):
        self.active_skill_session = skill_session
        self.active_skill_sessions[session_id] = skill_session


@pytest.fixture
def base_agent(monkeypatch):
    agent = Agent.__new__(Agent)
    agent.system_prompt = "BASE"
    agent.tools = [
        {"function": {"name": "allowed_tool", "description": "ok"}},
        {"function": {"name": "blocked_tool", "description": "no"}},
    ]
    agent.allowed_tool_names = {"allowed_tool", "blocked_tool"}
    agent.include_memory = False
    agent.think_level = SimpleNamespace(value="off")
    agent.model = None
    agent.agent_id = None
    agent.agent_name = None
    agent.memory_update_manager = None

    sess = _SessionManager()
    monkeypatch.setattr("src.agents.core.session_manager", sess)
    monkeypatch.setattr("src.skills.get_tracer", lambda: _Tracer())

    async def _no_fastlane(*args, **kwargs):
        return None

    monkeypatch.setattr("src.agents.fastlane.process_fastlane_command", _no_fastlane)
    monkeypatch.setattr("src.agents.core.memory_system", SimpleNamespace(build_context_with_search=lambda **kwargs: ""))
    return agent, sess


def test_skill_registry_parses_frontmatter_body_and_defaults(tmp_path):
    skill_file = tmp_path / "skill.md"
    skill_file.write_text(
        """---
name: runtime-test
description: runtime test skill
trigger:
  - runtime test
tools:
  - allowed_tool
---
# Body Title
Use compact instructions.
""",
        encoding="utf-8",
    )
    registry = SkillRegistry(project_skills_dir=str(tmp_path), user_skills_dir=str(tmp_path / "none"))
    skill = registry._load_skill_file(skill_file)
    assert skill is not None
    assert "Body Title" in skill.body
    assert skill.when_to_use == []
    assert skill.model == ""
    assert skill.hooks == []
    assert skill.task_tools == []


def test_parse_markdown_frontmatter_with_body_horizontal_rules(tmp_path):
    registry = SkillRegistry(project_skills_dir=str(tmp_path), user_skills_dir=str(tmp_path / "none"))
    frontmatter, body = registry._parse_markdown_frontmatter(
        """---
name: runtime-test
description: runtime test skill
---
Section A
---
Section B
"""
    )
    assert frontmatter["name"] == "runtime-test"
    assert "Section A" in body
    assert "\n---\n" in body


def test_parse_markdown_frontmatter_with_yaml_value_containing_dashes(tmp_path):
    registry = SkillRegistry(project_skills_dir=str(tmp_path), user_skills_dir=str(tmp_path / "none"))
    frontmatter, body = registry._parse_markdown_frontmatter(
        """---
name: runtime-test
description: "contains --- separators"
---
Body content
"""
    )
    assert frontmatter["description"] == "contains --- separators"
    assert body.strip() == "Body content"


def test_parse_markdown_frontmatter_unclosed_is_treated_as_no_frontmatter(tmp_path):
    registry = SkillRegistry(project_skills_dir=str(tmp_path), user_skills_dir=str(tmp_path / "none"))
    content = """---
name: runtime-test
description: runtime test skill
Body content without closing delimiter
"""
    frontmatter, body = registry._parse_markdown_frontmatter(content)
    assert frontmatter == {}
    assert body == content


def test_parse_markdown_frontmatter_non_mapping_yaml_is_safe(tmp_path, caplog):
    registry = SkillRegistry(project_skills_dir=str(tmp_path), user_skills_dir=str(tmp_path / "none"))
    with caplog.at_level("WARNING"):
        frontmatter, body = registry._parse_markdown_frontmatter(
            """---
- item1
- item2
---
Body
"""
        )
    assert frontmatter == {}
    assert body.strip() == "Body"
    assert "Invalid skill frontmatter type" in caplog.text


def test_parse_markdown_frontmatter_yaml_error_is_safe(tmp_path, caplog):
    registry = SkillRegistry(project_skills_dir=str(tmp_path), user_skills_dir=str(tmp_path / "none"))
    with caplog.at_level("WARNING"):
        frontmatter, body = registry._parse_markdown_frontmatter(
            """---
name: [unterminated
---
Body
"""
        )
    assert frontmatter == {}
    assert body.strip() == "Body"
    assert "Failed to parse skill markdown frontmatter" in caplog.text


def test_create_pull_request_skill_frontmatter_shape():
    registry = SkillRegistry(project_skills_dir="skills", user_skills_dir="/nonexistent/user/skills")
    skill = registry._load_skill_file(CREATE_PULL_REQUEST_SKILL_PATH)
    assert skill is not None
    assert skill.name == "create-pull-request"
    assert set(skill.tools) == {
        "run_command",
        "github_get_default_branch",
        "github_create_pull_request",
    }
    assert "run_command" in skill.task_tools
    assert "ref-template.md" in skill.references


def test_create_pull_request_runtime_config_contains_expected_blocks():
    registry = SkillRegistry(project_skills_dir="skills", user_skills_dir="/nonexistent/user/skills")
    skill = registry._load_skill_file(CREATE_PULL_REQUEST_SKILL_PATH)
    runtime_config = build_skill_runtime_config(skill)

    assert runtime_config.allowed_tools_set == {
        "run_command",
        "github_get_default_branch",
        "github_create_pull_request",
    }
    assert "ref-template.md" in runtime_config.prompt_blocks.references_summary
    instructions = runtime_config.prompt_blocks.developer_instructions
    assert ("STEP 1" in instructions) or ("Phase 1" in instructions)


def test_skill_contract_compiler_preserves_late_output_contract():
    filler = "\n".join(f"- filler line {idx}" for idx in range(1, 65))
    long_body = (
        "# Runtime Skill\n"
        "Intro text.\n\n"
        "## Workflow\n"
        f"{filler}\n\n"
        "## Output contract\n"
        "- Provide final checklist.\n\n"
        "## Reference usage\n"
        "- Cite provided references only.\n"
    )
    skill = SimpleNamespace(
        name="long-skill",
        description="desc",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body=long_body,
        references=[],
        model="",
        hooks=[],
        path="",
    )
    runtime_config = build_skill_runtime_config(skill)
    instructions = runtime_config.prompt_blocks.developer_instructions
    assert "Output contract" in instructions
    assert "Reference usage" in instructions


def test_skill_contract_compiler_preserves_priority_sections_when_truncated():
    body = (
        "# Huge Skill\n"
        "Intro\n\n"
        "## Workflow\n"
        + ("workflow filler\n" * 2000)
        + "\n## Output contract\n- Must return exact schema\n\n## Reference usage\n- Cite only provided references\n"
    )
    skill = SimpleNamespace(
        name="huge-skill",
        description="desc",
        tools=[],
        task_tools=[],
        strategy=[],
        body=body,
        references=[],
        model="",
        hooks=[],
        path="",
    )

    compiled = compile_skill_prompt_contract(skill, max_chars=2500)
    assert "Output contract" in compiled
    assert "Reference usage" in compiled
    assert "Skill contract truncated" in compiled
    assert len(compiled) <= 2600


def test_skill_contract_compiler_preserves_reference_usage_when_output_contract_is_huge():
    body = (
        "# Huge Skill\n"
        "Intro\n\n"
        "## Output contract\n"
        + ("schema detail line\n" * 1000)
        + "\n## Reference usage\n"
        "- Cite only provided references\n"
        "- Do not invent external facts\n\n"
        "## Constraints\n"
        "- Stay within provided context\n"
    )
    skill = SimpleNamespace(
        name="huge-output-contract",
        description="desc",
        tools=[],
        task_tools=[],
        strategy=[],
        body=body,
        references=[],
        model="",
        hooks=[],
        path="",
    )

    compiled = compile_skill_prompt_contract(skill, max_chars=2500)

    assert "Output contract" in compiled
    assert "Reference usage" in compiled
    assert "Cite only provided references" in compiled
    assert "Constraints" in compiled
    assert "Skill contract truncated" in compiled
    assert len(compiled) <= 2600


def test_skill_contract_compiler_preserves_all_critical_headings_under_tight_budget():
    body = (
        "# Huge Skill\n"
        + ("intro filler\n" * 200)
        + "\n## Output contract\n"
        + ("output filler\n" * 300)
        + "\n## Reference usage\n"
        + ("reference filler\n" * 300)
        + "\n## Constraints\n"
        + ("constraint filler\n" * 300)
        + "\n## Rules\n"
        + ("rule filler\n" * 300)
        + "\n## Tool policy\n"
        + ("tool policy filler\n" * 300)
        + "\n## Workflow\n"
        + ("workflow filler\n" * 1000)
    )
    skill = SimpleNamespace(
        name="tight-budget-skill",
        description="desc",
        tools=[],
        task_tools=[],
        strategy=[],
        body=body,
        references=[],
        model="",
        hooks=[],
        path="",
    )

    compiled = compile_skill_prompt_contract(skill, max_chars=3500)

    for heading in ["Output contract", "Reference usage", "Constraints", "Rules", "Tool policy"]:
        assert heading in compiled
    assert "Skill contract truncated" in compiled
    assert len(compiled) <= 3600


@pytest.mark.asyncio
async def test_generate_initial_skill_plan_routes_through_execution_bus(monkeypatch):
    skill = SimpleNamespace(name="demo", description="demo desc", strategy=[], path="")

    calls = {"count": 0, "kwargs": None}

    async def _fake_execute_skill_orchestration(**kwargs):
        calls["count"] += 1
        calls["kwargs"] = kwargs
        return SimpleNamespace(
            status="success",
            output_payload={"goal": "g", "steps": [{"id": "1", "type": "execute", "title": "t"}], "usage": {}},
        )

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.execute_skill_orchestration", _fake_execute_skill_orchestration)

    goal, steps, usage = await generate_initial_skill_plan(skill, "do work", return_usage=False)
    assert goal == "g"
    assert steps[0]["id"] == "1"
    assert usage == {}
    assert calls["count"] == 1
    assert calls["kwargs"]["source_ref"] == "skill_mode.generate_initial_skill_plan"

    goal2, steps2, usage2 = await generate_initial_skill_plan(skill, "do work", return_usage=True)
    assert goal2 == "g"
    assert steps2[0]["id"] == "1"
    assert usage2 == {}


@pytest.mark.asyncio
async def test_generate_initial_skill_plan_falls_back_to_direct_on_bus_error(monkeypatch):
    skill = SimpleNamespace(name="demo", description="demo desc", strategy=[], path="")

    class _FakeBus:
        def register_handler(self, execution_type, handler):
            self._handler = handler

        async def execute(self, request):
            return type("R", (), {"status": "error", "output_payload": {"error": "x"}})()

    async def _direct(*args, **kwargs):
        return {"goal": "direct-goal", "steps": [{"id": "d1", "type": "execute", "title": "direct"}], "usage": {"total_tokens": 5}}

    monkeypatch.setattr("src.runtime.build_default_execution_bus", lambda *args, **kwargs: _FakeBus())
    monkeypatch.setattr("src.agents.skill_mode._generate_initial_skill_plan_direct", _direct)

    goal, steps, usage = await generate_initial_skill_plan(skill, "do work")
    assert goal == "direct-goal"
    assert steps[0]["id"] == "d1"
    assert usage["total_tokens"] == 5


@pytest.mark.asyncio
async def test_generate_initial_skill_plan_normalizes_malformed_payload(monkeypatch):
    skill = SimpleNamespace(name="demo", description="demo desc", strategy=[], path="")

    async def _fake_execute_skill_orchestration(**kwargs):
        return SimpleNamespace(status="success", output_payload={"goal": "", "steps": []})

    monkeypatch.setattr("src.runtime.chat_orchestration_adapter.execute_skill_orchestration", _fake_execute_skill_orchestration)
    goal, steps, usage = await generate_initial_skill_plan(skill, "do work")
    assert goal == "demo desc"
    assert steps  # fallback from _normalize_plan should not be empty
    assert isinstance(usage, dict)


def test_prompt_layer_assembly_and_reference_context():
    skill = SimpleNamespace(
        name="compact",
        description="desc",
        tools=["a"],
        task_tools=["b"],
        strategy=["step1"],
        body="line1\nline2",
        references=["/tmp/ref-a.md", "/tmp/ref-b.md"],
        model="",
        hooks=[],
        path="",
    )
    runtime_config = build_skill_runtime_config(skill)
    assembly = assemble_effective_prompt("BASE", runtime_config)
    final_system_prompt, boundary_mode = resolve_prompt_execution_boundary(assembly)

    assert "Runtime policy" in assembly.system_rules_text
    assert "Skill: compact" in assembly.developer_instructions_text
    assert "Available references:" in assembly.reference_context_text
    assert "ref-a.md" in assembly.reference_context_text
    assert "line1" not in assembly.reference_context_text
    assert assembly.serialized_system_prompt.count("Skill Developer Instructions") == 1
    assert final_system_prompt == assembly.serialized_system_prompt
    assert boundary_mode == "merged_once_into_system"

    attachment = attach_skill_references(runtime_config)
    assert attachment.references == ["/tmp/ref-a.md", "/tmp/ref-b.md"]
    assert "Available references:" in attachment.context_text
    assert runtime_config.allowed_tools_set == {"a"}


def test_build_skill_runtime_config_scans_references_once(monkeypatch):
    skill = SimpleNamespace(
        name="compact",
        description="desc",
        tools=["a"],
        task_tools=[],
        strategy=[],
        body="line1",
        references=[],
        model="",
        hooks=[],
        path="",
    )
    calls = {"count": 0}

    def _fake_summarize(_skill):
        calls["count"] += 1
        return ["/tmp/ref-a.md"]

    monkeypatch.setattr("src.skills.runtime.summarize_skill_references", _fake_summarize)
    runtime_config = build_skill_runtime_config(skill)
    assert calls["count"] == 1
    assert runtime_config.references == ["/tmp/ref-a.md"]
    assert "ref-a.md" in runtime_config.prompt_blocks.references_summary


def test_build_skill_runtime_config_intersects_declared_tools_with_global_allowlist():
    skill = SimpleNamespace(
        name="demo",
        description="demo",
        tools=["git_clone", "jira_get_issue"],
        task_tools=["git_clone", "jira_get_issue"],
        strategy=[],
        body="",
        references=[],
        model="",
        hooks=[],
        path="",
    )
    runtime_config = build_skill_runtime_config(skill, globally_allowed_tool_names={"git_clone"})

    assert runtime_config.tool_policy_declared is True
    assert runtime_config.allowed_tools == ["git_clone"]
    assert runtime_config.allowed_tools_set == {"git_clone"}
    assert runtime_config.task_tools == ["git_clone"]
    assert "git_clone" in runtime_config.prompt_blocks.system_rules
    assert "jira_get_issue" not in runtime_config.prompt_blocks.system_rules


def test_build_skill_runtime_config_declared_tools_empty_after_intersection():
    skill = SimpleNamespace(
        name="demo",
        description="demo",
        tools=["jira_get_issue"],
        task_tools=[],
        strategy=[],
        body="",
        references=[],
        model="",
        hooks=[],
        path="",
    )
    runtime_config = build_skill_runtime_config(skill, globally_allowed_tool_names={"git_clone"})

    assert runtime_config.tool_policy_declared is True
    assert runtime_config.allowed_tools == []
    assert runtime_config.allowed_tools_set == set()
    assert "allowed tools (none)" in runtime_config.prompt_blocks.system_rules
    assert "all currently available tools" not in runtime_config.prompt_blocks.system_rules


def test_build_skill_runtime_config_without_declared_tools_keeps_policy_open():
    skill = SimpleNamespace(
        name="demo",
        description="demo",
        tools=[],
        task_tools=[],
        strategy=[],
        body="",
        references=[],
        model="",
        hooks=[],
        path="",
    )
    runtime_config = build_skill_runtime_config(skill, globally_allowed_tool_names={"git_clone"})

    assert runtime_config.tool_policy_declared is False
    assert runtime_config.allowed_tools == []
    assert "all currently available tools" in runtime_config.prompt_blocks.system_rules


def test_build_skill_runtime_config_task_tools_trimmed_by_global_allowlist():
    skill = SimpleNamespace(
        name="demo",
        description="demo",
        tools=["git_clone", "jira_get_issue"],
        task_tools=["jira_get_issue"],
        strategy=[],
        body="",
        references=[],
        model="",
        hooks=[],
        path="",
    )
    runtime_config = build_skill_runtime_config(skill, globally_allowed_tool_names={"git_clone"})

    assert runtime_config.task_tools == []


def test_build_skill_tool_denied_result_contains_policy():
    runtime_config = build_skill_runtime_config(
        SimpleNamespace(
            name="demo",
            description="demo",
            tools=["allowed_tool"],
            task_tools=[],
            strategy=[],
            body="",
            references=[],
            model="",
            hooks=[],
            path="",
        )
    )
    denied = build_skill_tool_denied_result(runtime_config, "blocked_tool")
    assert denied.success is False
    assert "blocked_tool" in str(denied)
    assert "allowed_tool" in str(denied)


@pytest.mark.asyncio
async def test_matched_skill_does_not_route_to_legacy_skill_mode(monkeypatch, base_agent):
    agent, _ = base_agent
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=[],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(s, globally_allowed_tool_names=globally_allowed_tool_names)),
    )

    async def fake_responses(**kwargs):
        return {"content": "done", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    async def fail_start(*args, **kwargs):
        raise AssertionError("legacy _start_skill_mode should not be called")

    async def fail_continue(*args, **kwargs):
        raise AssertionError("legacy _continue_skill_mode should not be called")

    monkeypatch.setattr(agent, "_start_skill_mode", fail_start)
    monkeypatch.setattr(agent, "_continue_skill_mode", fail_continue)

    result = await agent.process("runtime test", session_id="s1")
    assert result["response"] == "done"


@pytest.mark.asyncio
async def test_active_skill_contract_continues_without_rematch(monkeypatch, base_agent):
    agent, sess = base_agent
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="Follow runtime contract instructions.",
        references=[],
        model="",
        hooks=[],
        deprecated=False,
    )

    class _Registry:
        _initialized = True

        def __init__(self):
            self.match_calls = 0

        def match_skill(self, *_args, **_kwargs):
            self.match_calls += 1
            if self.match_calls == 1:
                return [matched_skill]
            return []

        def get_skill(self, name):
            if name == matched_skill.name:
                return matched_skill
            return None

        def get_skill_runtime_config(self, skill, globally_allowed_tool_names=None):
            return build_skill_runtime_config(skill, globally_allowed_tool_names=globally_allowed_tool_names)

    monkeypatch.setattr("src.skills.skill_registry", _Registry())

    captured_system_prompts = []

    async def fake_responses(**kwargs):
        captured_system_prompts.append(kwargs.get("system_prompt", ""))
        return {"content": "done", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    async def fail_start(*args, **kwargs):
        raise AssertionError("legacy _start_skill_mode should not be called")

    async def fail_continue(*args, **kwargs):
        raise AssertionError("legacy _continue_skill_mode should not be called")

    monkeypatch.setattr(agent, "_start_skill_mode", fail_start)
    monkeypatch.setattr(agent, "_continue_skill_mode", fail_continue)

    first = await agent.process("runtime test", session_id="s1")
    second = await agent.process("continue", session_id="s1")

    assert first["response"] == "done"
    assert second["response"] == "done"
    assert len(captured_system_prompts) == 2
    assert "Active skill: runtime-skill" in captured_system_prompts[1]
    assert "Skill Developer Instructions" in captured_system_prompts[1]
    assert "Follow runtime contract instructions." in captured_system_prompts[1]
    assert isinstance(sess.active_skill_session, dict)
    assert sess.active_skill_session.get("turn_count", 0) >= 2


@pytest.mark.asyncio
async def test_active_skill_contract_does_not_soft_switch_on_ordinary_match(monkeypatch, base_agent):
    agent, sess = base_agent
    review_skill = SimpleNamespace(
        name="review-pull-request",
        description="review",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="Review instructions",
        references=[],
        model="",
        hooks=[],
        deprecated=False,
    )
    create_skill = SimpleNamespace(
        name="create-pull-request",
        description="create",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="Create instructions",
        references=[],
        model="",
        hooks=[],
        deprecated=False,
    )
    sess.active_skill_session = {
        "schema_version": "active_skill_contract.v1",
        "skill_name": "review-pull-request",
        "status": "active",
        "turn_count": 1,
    }

    class _Registry:
        _initialized = True

        def match_skill(self, message):
            if "ordinary message that matches create" in message:
                return [create_skill]
            return []

        def get_skill(self, name):
            if name == review_skill.name:
                return review_skill
            if name == create_skill.name:
                return create_skill
            return None

        def get_skill_runtime_config(self, skill, globally_allowed_tool_names=None):
            return build_skill_runtime_config(skill, globally_allowed_tool_names=globally_allowed_tool_names)

    monkeypatch.setattr("src.skills.skill_registry", _Registry())
    captured_prompts = []

    async def fake_responses(**kwargs):
        captured_prompts.append(kwargs.get("system_prompt", ""))
        return {"content": "done", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    result = await agent.process("ordinary message that matches create", session_id="s1")
    assert result["response"] == "done"
    assert captured_prompts
    assert "Active skill: review-pull-request" in captured_prompts[0]
    assert "Active skill: create-pull-request" not in captured_prompts[0]
    assert sess.active_skill_session["skill_name"] == "review-pull-request"
    assert sess.active_skill_session["activation_reason"] == "continued"


@pytest.mark.asyncio
async def test_active_skill_contract_switches_only_on_explicit_skill_command(monkeypatch, base_agent):
    agent, sess = base_agent
    review_skill = SimpleNamespace(
        name="review-pull-request",
        description="review",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="Review instructions",
        references=[],
        model="",
        hooks=[],
        deprecated=False,
    )
    create_skill = SimpleNamespace(
        name="create-pull-request",
        description="create",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="Create instructions",
        references=[],
        model="",
        hooks=[],
        deprecated=False,
    )
    sess.active_skill_session = {
        "schema_version": "active_skill_contract.v1",
        "skill_name": "review-pull-request",
        "status": "active",
        "turn_count": 1,
    }

    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(
            _initialized=True,
            load_skills=lambda: None,
            match_skill=lambda *_: [],
            get_skill=lambda name: create_skill if name == "create-pull-request" else (review_skill if name == "review-pull-request" else None),
            get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(
                s, globally_allowed_tool_names=globally_allowed_tool_names
            ),
        ),
    )
    captured_prompts = []

    async def fake_responses(**kwargs):
        captured_prompts.append(kwargs.get("system_prompt", ""))
        return {"content": "done", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    result = await agent.process("/skill switch create-pull-request", session_id="s1")
    assert result["response"] == "done"
    assert "Active skill: create-pull-request" in captured_prompts[0]
    assert sess.active_skill_session["skill_name"] == "create-pull-request"
    assert sess.active_skill_session["activation_reason"] == "switched"


@pytest.mark.asyncio
async def test_explicit_skill_use_starts_skill_without_existing_contract(monkeypatch, base_agent):
    agent, sess = base_agent

    review_skill = SimpleNamespace(
        name="review-pull-request",
        description="review",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="Review instructions",
        references=[],
        model="",
        hooks=[],
        deprecated=False,
    )

    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(
            _initialized=True,
            load_skills=lambda: None,
            match_skill=lambda *_: [],
            get_skill=lambda name: review_skill if name == "review-pull-request" else None,
            get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(
                s, globally_allowed_tool_names=globally_allowed_tool_names
            ),
        ),
    )

    captured_prompts = []

    async def fake_responses(**kwargs):
        captured_prompts.append(kwargs.get("system_prompt", ""))
        return {"content": "done", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    result = await agent.process("/skill use review-pull-request", session_id="s1")

    assert result["response"] == "done"
    assert "Active skill: review-pull-request" in captured_prompts[0]
    assert sess.active_skill_session["skill_name"] == "review-pull-request"
    assert sess.active_skill_session["activation_reason"] == "matched"


@pytest.mark.asyncio
async def test_explicit_skill_activate_unknown_without_existing_contract_returns_without_llm(monkeypatch, base_agent):
    agent, sess = base_agent

    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(
            _initialized=True,
            load_skills=lambda: None,
            match_skill=lambda *_: [],
            get_skill=lambda *_: None,
            get_skill_runtime_config=lambda *args, **kwargs: None,
        ),
    )

    async def fake_responses(**kwargs):
        raise AssertionError("llm_client.responses should not be called for unknown explicit skill command")

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    result = await agent.process("/skill activate missing-skill", session_id="s1")

    assert "Skill not found: missing-skill" in result["response"]
    assert sess.active_skill_session is None


def test_parse_explicit_skill_switch_name_requires_name_for_switch_use_activate():
    from src.skills.active_contract import parse_explicit_skill_switch_name

    assert parse_explicit_skill_switch_name("/skill switch") == ""
    assert parse_explicit_skill_switch_name("/skill use") == ""
    assert parse_explicit_skill_switch_name("/skill activate") == ""
    assert parse_explicit_skill_switch_name("/skill use review-pull-request") == "review-pull-request"


@pytest.mark.asyncio
async def test_active_skill_contract_unknown_explicit_switch_returns_without_llm(monkeypatch, base_agent):
    agent, sess = base_agent
    sess.active_skill_session = {
        "schema_version": "active_skill_contract.v1",
        "skill_name": "review-pull-request",
        "status": "active",
        "turn_count": 1,
    }
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(
            _initialized=True,
            load_skills=lambda: None,
            match_skill=lambda *_: [],
            get_skill=lambda *_: None,
            get_skill_runtime_config=lambda *args, **kwargs: None,
        ),
    )

    async def fake_responses(**kwargs):
        raise AssertionError("llm_client.responses should not be called for unknown explicit skill switch")

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    result = await agent.process("/skill switch missing-skill", session_id="s1")
    assert "Skill not found: missing-skill" in result["response"]
    assert sess.active_skill_session["skill_name"] == "review-pull-request"


@pytest.mark.asyncio
async def test_clear_active_skill_command_emits_cleared_event_without_llm(monkeypatch, base_agent):
    agent, sess = base_agent
    sess.active_skill_session = {"skill_name": "runtime-skill", "status": "active"}
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(
            _initialized=True,
            load_skills=lambda: None,
            match_skill=lambda *_: [],
            get_skill=lambda *_: None,
            get_skill_runtime_config=lambda *args, **kwargs: None,
        ),
    )

    async def fake_responses(**kwargs):
        raise AssertionError("llm_client.responses should not be called for /skill clear")

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    events = []

    def stream_callback(event_json: str):
        events.append(event_json)

    result = await agent.process("/skill clear", session_id="s1", stream_callback=stream_callback)
    assert result["response"] == "Active skill cleared."
    assert sess.active_skill_session is None
    assert any('"type": "skill_contract_cleared"' in item for item in events)
    assert any('"skill": "runtime-skill"' in item for item in events)


@pytest.mark.asyncio
async def test_matched_skill_workdir_updates_and_clears_when_path_falsy(monkeypatch, base_agent, tmp_path):
    agent, _ = base_agent
    skill_with_path = SimpleNamespace(
        name="runtime-path",
        description="d",
        path=str(tmp_path),
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=[],
    )
    skill_without_path = SimpleNamespace(
        name="runtime-empty-path",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=[],
    )

    def _match_skill(message):
        if "with path" in message:
            return [skill_with_path]
        return [skill_without_path]

    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(
            _initialized=True,
            match_skill=_match_skill,
            get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(s, globally_allowed_tool_names=globally_allowed_tool_names),
        ),
    )

    async def fake_responses(**kwargs):
        return {"content": "done", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    set_skill_workdir(None)
    first = await agent.process("run with path", session_id="s-workdir-1")
    assert first["response"] == "done"
    assert get_skill_workdir() == str(tmp_path)

    second = await agent.process("run without path", session_id="s-workdir-2")
    assert second["response"] == "done"
    assert get_skill_workdir() is None


@pytest.mark.asyncio
async def test_disallowed_tool_is_denied_and_allowed_tool_executes(monkeypatch, base_agent):
    agent, _ = base_agent
    events = []

    def stream_callback(event_json: str):
        events.append(event_json)

    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=["/tmp/ref-a.md"],
        model="",
        hooks=[],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(s, globally_allowed_tool_names=globally_allowed_tool_names)),
    )

    calls = {"execute": 0, "names": []}

    async def fake_execute(name, **kwargs):
        calls["execute"] += 1
        calls["names"].append(name)
        return ToolResult(True, "tool ok")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", fake_execute)

    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "blocked_tool", "arguments": "{}"}], "usage": {}},
        {"content": "", "function_calls": [{"call_id": "2", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))
    result = await agent.process("runtime test", session_id="s2", stream_callback=stream_callback)
    assert result["response"] == "done"
    assert calls["execute"] == 1
    assert calls["names"] == ["allowed_tool"]
    assert any('"type": "skill_tool_denied"' in e for e in events)
    assert any('"type": "tool_result"' in e and "blocked_tool" in e for e in events)


@pytest.mark.asyncio
async def test_skill_declared_tools_empty_intersection_sends_empty_llm_tool_surface(monkeypatch, base_agent):
    agent, _ = base_agent
    agent.tools = [{"function": {"name": "allowed_tool", "description": "ok"}}]
    agent.allowed_tool_names = {"allowed_tool"}

    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["blocked_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=[],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(
            _initialized=True,
            match_skill=lambda *_: [matched_skill],
            get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(
                s,
                globally_allowed_tool_names=globally_allowed_tool_names,
            ),
        ),
    )

    seen_llm_tools = []
    tool_exec_calls = {"count": 0}

    async def _fake_execute_tool(*args, **kwargs):
        tool_exec_calls["count"] += 1
        return ToolResult(True, "unexpected")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _fake_execute_tool)

    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "blocked_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def _fake_responses(**kwargs):
        seen_llm_tools.append(kwargs.get("tools"))
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=_fake_responses, default_provider="openai"))

    result = await agent.process("runtime test", session_id="s-empty-intersection")
    assert result["response"] == "done"
    assert seen_llm_tools
    assert seen_llm_tools[0] == []
    assert tool_exec_calls["count"] == 0


@pytest.mark.asyncio
async def test_hooks_and_task_path_emit_events(monkeypatch, base_agent):
    agent, _ = base_agent
    events = []

    def stream_callback(event_json: str):
        events.append(event_json)

    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=["allowed_tool"],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=["pre_tool", "post_tool"],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(s, globally_allowed_tool_names=globally_allowed_tool_names)),
    )

    async def _exec(*args, **kwargs):
        return ToolResult(True, "task ok")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _exec)

    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))

    result = await agent.process("runtime test", session_id="s3", stream_callback=stream_callback)
    assert result["response"] == "done"
    assert any('"type": "skill_hook"' in e for e in events)
    assert any('"type": "task_started"' in e for e in events)
    assert any('"type": "task_finished"' in e for e in events)


@pytest.mark.asyncio
async def test_hook_failure_does_not_break_request(monkeypatch, base_agent):
    agent, _ = base_agent
    monkeypatch.setenv("SKILL_RUNTIME_ENABLE_TEST_HOOKS", "1")
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=["pre_tool:tests.test_skill_runtime_refactor._failing_hook"],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(s, globally_allowed_tool_names=globally_allowed_tool_names)),
    )

    async def _exec(*args, **kwargs):
        return ToolResult(True, "task ok")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _exec)

    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))
    result = await agent.process("runtime test", session_id="s-hook-fail")
    assert result["response"] == "done"


@pytest.mark.asyncio
async def test_pre_hook_can_modify_args(monkeypatch, base_agent):
    agent, _ = base_agent
    monkeypatch.setenv("SKILL_RUNTIME_ENABLE_TEST_HOOKS", "1")
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=["pre_tool:tests.test_skill_runtime_refactor._modify_args_hook"],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(s, globally_allowed_tool_names=globally_allowed_tool_names)),
    )
    captured = {}

    async def _exec(name=None, **kwargs):
        captured.update(kwargs)
        return ToolResult(True, "ok")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _exec)
    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "allowed_tool", "arguments": '{"a":"1"}'}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]
    async def _fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=_fake_responses, default_provider="openai"))
    result = await agent.process("runtime test", session_id="s-mod-args")
    assert result["response"] == "done"
    assert captured.get("a") == "2"


@pytest.mark.asyncio
async def test_pre_hook_can_short_circuit(monkeypatch, base_agent):
    agent, _ = base_agent
    events = []

    def stream_callback(event_json: str):
        events.append(event_json)

    monkeypatch.setenv("SKILL_RUNTIME_ENABLE_TEST_HOOKS", "1")
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=["pre_tool:tests.test_skill_runtime_refactor._short_circuit_hook"],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(s, globally_allowed_tool_names=globally_allowed_tool_names)),
    )
    called = {"tool": 0}

    async def _exec(*args, **kwargs):
        called["tool"] += 1
        return ToolResult(True, "should not execute")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _exec)
    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]
    async def _fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=_fake_responses, default_provider="openai"))
    result = await agent.process("runtime test", session_id="s-short", stream_callback=stream_callback)
    assert result["response"] == "done"
    assert called["tool"] == 0
    assert any('"type": "tool_result"' in e and "allowed_tool" in e for e in events)


@pytest.mark.asyncio
async def test_pre_hook_short_circuit_failure_emits_failed_tool_result(monkeypatch, base_agent):
    agent, _ = base_agent
    events = []

    def stream_callback(event_json: str):
        events.append(event_json)

    monkeypatch.setenv("SKILL_RUNTIME_ENABLE_TEST_HOOKS", "1")
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=["pre_tool:tests.test_skill_runtime_refactor._short_circuit_fail_hook"],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(s, globally_allowed_tool_names=globally_allowed_tool_names)),
    )
    called = {"tool": 0}

    async def _exec(*args, **kwargs):
        called["tool"] += 1
        return ToolResult(True, "should not execute")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _exec)
    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]

    async def _fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=_fake_responses, default_provider="openai"))
    result = await agent.process("runtime test", session_id="s-short-fail", stream_callback=stream_callback)
    assert result["response"] == "done"
    assert called["tool"] == 0
    assert any('"type": "tool_result"' in e and '"success": false' in e for e in events)


@pytest.mark.asyncio
async def test_post_hook_can_override_result(monkeypatch, base_agent):
    agent, _ = base_agent
    monkeypatch.setenv("SKILL_RUNTIME_ENABLE_TEST_HOOKS", "1")
    matched_skill = SimpleNamespace(
        name="runtime-skill",
        description="d",
        path="",
        tools=["allowed_tool"],
        task_tools=[],
        strategy=[],
        body="instructions",
        references=[],
        model="",
        hooks=["post_tool:tests.test_skill_runtime_refactor._post_override_hook"],
    )
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [matched_skill], get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: build_skill_runtime_config(s, globally_allowed_tool_names=globally_allowed_tool_names)),
    )

    async def _exec(*args, **kwargs):
        return ToolResult(True, "orig-result")

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _exec)
    responses = [
        {"content": "", "function_calls": [{"call_id": "1", "name": "allowed_tool", "arguments": "{}"}], "usage": {}},
        {"content": "done", "function_calls": [], "usage": {}},
    ]
    async def _fake_responses(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=_fake_responses, default_provider="openai"))
    result = await agent.process("runtime test", session_id="s-post")
    assert result["response"] == "done"


@pytest.mark.asyncio
async def test_non_skill_request_unaffected(monkeypatch, base_agent):
    agent, _ = base_agent
    monkeypatch.setattr(
        "src.skills.skill_registry",
        SimpleNamespace(_initialized=True, match_skill=lambda *_: [], get_skill_runtime_config=lambda s, globally_allowed_tool_names=None: None),
    )

    async def fake_responses(**kwargs):
        return {"content": "plain response", "function_calls": [], "usage": {}}

    monkeypatch.setattr("src.agents.core.llm_client", SimpleNamespace(responses=fake_responses, default_provider="openai"))
    result = await agent.process("hello", session_id="s4")
    assert result["response"] == "plain response"


def _failing_hook(context):
    raise RuntimeError("hook failure for testing")


def _modify_args_hook(context):
    return {"modified_args": {"a": "2"}}


def _short_circuit_hook(context):
    return {"short_circuit_result": {"success": True, "content": "short-circuit"}}


def _short_circuit_fail_hook(context):
    return {"short_circuit_result": {"success": False, "content": "short-circuit-fail"}}


def _post_override_hook(context):
    return {"result_override": {"success": True, "content": "overridden-result"}}


def test_reference_fallback_scoping_for_single_file_skill(tmp_path):
    shared = tmp_path / "skills"
    shared.mkdir()
    skill_file = shared / "alpha.md"
    skill_file.write_text("---\nname: alpha\ndescription: a\n---\n", encoding="utf-8")
    (shared / "README.md").write_text("no", encoding="utf-8")
    (shared / "beta.md").write_text("---\nname: beta\ndescription: b\n---\n", encoding="utf-8")
    (shared / "ref-alpha-guide.md").write_text("guide", encoding="utf-8")

    skill = Skill(name="alpha", description="a", path=str(shared), source_file=str(skill_file))
    refs = summarize_skill_references(skill)
    assert any("ref-alpha-guide.md" in r for r in refs)
    assert all("beta.md" not in r for r in refs)
    assert all("README.md" not in r for r in refs)


def test_explicit_references_take_precedence(tmp_path):
    explicit = str(tmp_path / "x.md")
    skill = Skill(name="alpha", description="a", references=[explicit], path=str(tmp_path))
    refs = summarize_skill_references(skill)
    assert refs == [explicit]


def test_explicit_relative_references_resolve_against_skill_path(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    skill = Skill(
        name="alpha",
        description="a",
        path=str(skill_dir),
        references=["references/playbook.md", "  ", "notes.md"],
    )
    refs = summarize_skill_references(skill)
    assert refs == [
        str((skill_dir / "references/playbook.md").resolve()),
        str((skill_dir / "notes.md").resolve()),
    ]


def test_explicit_references_fallback_to_source_file_parent(tmp_path):
    skill_file = tmp_path / "nested" / "skill.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("---\nname: alpha\ndescription: a\n---\n", encoding="utf-8")
    skill = Skill(
        name="alpha",
        description="a",
        source_file=str(skill_file),
        references=["references/readme.md"],
    )
    refs = summarize_skill_references(skill)
    assert refs == [str((skill_file.parent / "references/readme.md").resolve())]


def test_reference_normalization_fallback(monkeypatch):
    monkeypatch.chdir("/tmp")
    skill = Skill(
        name="alpha",
        description="a",
        path="",
        source_file="",
        references=["references/playbook.md"],
    )
    refs = summarize_skill_references(skill)
    assert refs == [str((Path("/tmp") / "references/playbook.md").resolve())]


def test_hook_resolution_rejects_unapproved_import(monkeypatch):
    import_called = {"value": False}

    def _fail_import(name):
        import_called["value"] = True
        raise AssertionError("import should not occur for unapproved target")

    monkeypatch.setattr("src.agents.skill_runtime.import_module", _fail_import)
    result = dispatch_skill_hook(
        hook_name="pre_tool:os.system",
        context={"session_id": "s", "skill_name": "k", "tool_name": "t", "stage": "pre_tool", "payload": {}},
    )
    assert result["mode"] == "rejected_unapproved_hook"
    assert result["applied"] is False
    assert import_called["value"] is False


def test_dispatch_skill_hook_event_only_mode():
    result = dispatch_skill_hook(
        hook_name="pre_tool",
        context={"session_id": "s", "skill_name": "k", "tool_name": "t", "stage": "pre_tool", "payload": {}},
    )
    assert result["mode"] == "event_only"
    assert result["applied"] is True


def test_dispatch_skill_hook_approved_callable_mode(monkeypatch):
    hook_module = SimpleNamespace(test_hook=lambda context: {"modified_args": {"x": "1"}})
    monkeypatch.setattr("src.agents.skill_runtime.import_module", lambda _name: hook_module)
    result = dispatch_skill_hook(
        hook_name="pre_tool:src.hooks.fake.test_hook",
        context={"session_id": "s", "skill_name": "k", "tool_name": "t", "stage": "pre_tool", "payload": {}},
    )
    assert result["mode"] == "callable"
    assert result["applied"] is True
    assert result["hook_effects"]["modified_args"] == {"x": "1"}


def test_dispatch_skill_hook_ignores_unknown_effect_keys(monkeypatch):
    hook_module = SimpleNamespace(_unknown_effect_hook=_unknown_effect_hook)
    monkeypatch.setattr("src.agents.skill_runtime.import_module", lambda _name: hook_module)
    result = dispatch_skill_hook(
        hook_name="pre_tool:src.hooks.fake._unknown_effect_hook",
        context={"session_id": "s", "skill_name": "k", "tool_name": "t", "stage": "pre_tool", "payload": {}},
    )
    assert result["mode"] == "callable"
    assert "unknown_key" not in result.get("hook_effects", {})


def test_dispatch_skill_hook_rejects_async_hook(monkeypatch):
    hook_module = SimpleNamespace(_async_hook=_async_hook)
    monkeypatch.setattr("src.agents.skill_runtime.import_module", lambda _name: hook_module)
    result = dispatch_skill_hook(
        hook_name="pre_tool:src.hooks.fake._async_hook",
        context={"session_id": "s", "skill_name": "k", "tool_name": "t", "stage": "pre_tool", "payload": {}},
    )
    assert result["mode"] == "unsupported_async_hook"
    assert result["applied"] is False


@pytest.mark.asyncio
async def test_task_lifecycle_events_and_retention():
    events = []
    manager = TaskManager(max_completed_tasks=2)

    def _event(name, data):
        events.append((name, data))

    async def _ok():
        return "ok"

    async def _boom():
        raise RuntimeError("boom")

    first_task = await manager.submit_tool_task(session_id="s", tool_name="t1", coro_factory=_ok, event_callback=_event)
    with pytest.raises(RuntimeError):
        await manager.submit_tool_task(session_id="s", tool_name="t2", coro_factory=_boom, event_callback=_event)
    await manager.submit_tool_task(session_id="s", tool_name="t3", coro_factory=_ok, event_callback=_event)

    names = [name for name, _ in events]
    assert "task_queued" in names
    assert "task_started" in names
    assert "task_finished" in names
    assert "task_failed" in names
    assert manager.get_task(first_task.task_id) is None


@pytest.mark.asyncio
async def test_task_manager_uses_enqueue_task_alias(monkeypatch):
    manager = TaskManager()
    called = {"enqueue_task": 0}

    async def _fake_enqueue_task(session_id, coro, *args, **kwargs):
        called["enqueue_task"] += 1
        return await coro(*args, **kwargs)

    monkeypatch.setattr("src.agents.tasks.execution_queue.enqueue_task", _fake_enqueue_task)

    async def _ok():
        return "ok"

    task = await manager.submit_tool_task(session_id="s", tool_name="t", coro_factory=_ok)
    assert task.status == "completed"
    assert task.result == "ok"
    assert called["enqueue_task"] == 1


def test_task_retention_preserves_active_records():
    manager = TaskManager(max_completed_tasks=1)
    manager._tasks["running"] = TaskRecord(task_id="running", session_id="s", tool_name="x", status="running")
    manager._tasks["c1"] = TaskRecord(task_id="c1", session_id="s", tool_name="x", status="completed", finished_at="2020-01-01T00:00:00Z")
    manager._tasks["c2"] = TaskRecord(task_id="c2", session_id="s", tool_name="x", status="completed", finished_at="2020-01-02T00:00:00Z")
    manager._prune_completed_tasks()
    assert "running" in manager._tasks
    assert len([t for t in manager._tasks.values() if t.status == "completed"]) == 1


def test_skill_runtime_event_payload_sanitized_and_verbose():
    runtime_config = build_skill_runtime_config(
        SimpleNamespace(
            name="demo",
            description="d",
            tools=["allowed_tool"],
            task_tools=[],
            strategy=[],
            body="secret-body",
            references=["/tmp/private/path/ref-1.md"],
            model="",
            hooks=[],
            path="",
        )
    )
    assembly = assemble_effective_prompt("BASE", runtime_config)
    attachment = attach_skill_references(runtime_config)

    payload = build_skill_runtime_event_payload(
        runtime_config=runtime_config,
        reference_attachment=attachment,
        prompt_assembly=assembly,
        prompt_boundary_mode="merged_once_into_system",
        verbose=False,
    )
    assert payload["references"] == ["ref-1.md"]
    assert "prompt_layers" not in payload
    assert "ref-1.md" not in payload["reference_context"]

    verbose_payload = build_skill_runtime_event_payload(
        runtime_config=runtime_config,
        reference_attachment=attachment,
        prompt_assembly=assembly,
        prompt_boundary_mode="merged_once_into_system",
        verbose=True,
    )
    assert "prompt_layers" in verbose_payload


def _unknown_effect_hook(context):
    return {"unknown_key": "ignored", "modified_args": {"x": "1"}}


async def _async_hook(context):
    return {"modified_args": {"x": "async"}}


def test_get_skill_prompt_includes_resolved_reference_summary(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    registry = SkillRegistry(project_skills_dir=str(tmp_path), user_skills_dir=str(tmp_path / "none"))
    skill = Skill(
        name="alpha",
        description="a",
        path=str(skill_dir),
        references=["references/playbook.md"],
        tools=["allowed_tool"],
    )
    prompt = registry.get_skill_prompt(skill)
    assert "Active skill: alpha" in prompt
    assert "Available references: playbook.md" in prompt


def test_review_pull_request_skill_loads_from_directory_structure():
    registry = SkillRegistry(project_skills_dir=str(REPO_ROOT / "skills"), user_skills_dir="/nonexistent/user/skills")
    registry.load_skills()
    skill = registry.get_skill("review-pull-request")
    assert skill is not None
    assert Path(skill.source_file).as_posix().endswith("skills/review-pull-request/skill.md")
    assert "github_get_pr" in skill.tools
    assert "github_get_pr_file_patch" in skill.tools


def test_review_pull_request_skill_references_are_discovered():
    registry = SkillRegistry(project_skills_dir="skills", user_skills_dir="/nonexistent/user/skills")
    registry.load_skills()
    skill = registry.get_skill("review-pull-request")
    refs = summarize_skill_references(skill)
    assert any(path.endswith("review-pull-request-guidelines.md") for path in refs)
    assert any(path.endswith("lang-typescript-javascript.md") for path in refs)
    assert any(path.endswith("lang-python.md") for path in refs)


def test_review_pull_request_backward_compatible_trigger_invocation():
    registry = SkillRegistry(project_skills_dir=str(REPO_ROOT / "skills"), user_skills_dir="/nonexistent/user/skills")
    registry.load_skills()
    matches = registry.match_skill("/review-pr 123")
    assert matches
    assert matches[0].name == "review-pull-request"


def test_review_pull_request_backward_compatible_skill_command_invocation():
    registry = SkillRegistry(project_skills_dir=str(REPO_ROOT / "skills"), user_skills_dir="/nonexistent/user/skills")
    registry.load_skills()
    matches = registry.match_skill("/skill review-pr")
    assert matches
    assert matches[0].name == "review-pull-request"


@pytest.mark.asyncio
async def test_core_tool_bus_helper_returns_tool_result_compatible_shape(monkeypatch):
    async def _fake_execute_tool_or_task_orchestration(**kwargs):
        return make_execution_result(
            request_id="req-1",
            status="success",
            output_payload={"success": True, "content": "tool-ok", "error": None},
        )

    monkeypatch.setattr("src.agents.core.execute_tool_or_task_orchestration", _fake_execute_tool_or_task_orchestration)
    result = await _execute_tool_via_runtime_bus(
        session_id="s1",
        tool_name="demo_tool",
        args={"value": 1},
        runtime_config=None,
        source_ref="test",
    )

    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.content == "tool-ok"


@pytest.mark.asyncio
async def test_core_tool_bus_helper_builds_bus_without_stale_execute_direct_kwarg(monkeypatch):
    captured_kwargs = {}

    async def _fake_execute_tool_or_task_orchestration(**kwargs):
        captured_kwargs.update(kwargs)
        return make_execution_result(
            request_id="req-2",
            status="success",
            output_payload={"success": True, "content": "ok"},
        )

    monkeypatch.setattr("src.agents.core.execute_tool_or_task_orchestration", _fake_execute_tool_or_task_orchestration)
    await _execute_tool_via_runtime_bus(
        session_id="s1",
        tool_name="demo_tool",
        args={},
        runtime_config=None,
        source_ref="test",
    )

    assert "execute_tool_func" in captured_kwargs
    assert "execute_direct" not in captured_kwargs


@pytest.mark.asyncio
async def test_core_tool_bus_helper_uses_patched_core_execute_tool_callable_for_tool_and_task(monkeypatch):
    calls = []

    async def _patched_execute_tool_by_name(tool_name, **kwargs):
        calls.append((tool_name, kwargs))
        return ToolResult(success=True, content=f"patched:{tool_name}", error=None)

    async def _inline_task_runner(*, session_id, tool_name, coro_factory, event_callback=None):
        return await coro_factory()

    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _patched_execute_tool_by_name)
    monkeypatch.setattr("src.runtime.execution_bus.task_manager.run_tool_task", _inline_task_runner)

    tool_result = await _execute_tool_via_runtime_bus(
        session_id="s-tool",
        tool_name="tool_plain",
        args={"a": 1},
        runtime_config=None,
        source_ref="test",
    )
    task_result = await _execute_tool_via_runtime_bus(
        session_id="s-task",
        tool_name="tool_tasked",
        args={"b": 2},
        runtime_config=SimpleNamespace(task_tools=["tool_tasked"]),
        source_ref="test",
    )

    assert tool_result.content == "patched:tool_plain"
    assert task_result.content == "patched:tool_tasked"
    assert calls == [("tool_plain", {"a": 1}), ("tool_tasked", {"b": 2})]


@pytest.mark.asyncio
async def test_core_tool_bus_helper_does_not_persist_last_execution_id_by_default(monkeypatch):
    persist_calls = []

    async def _fake_set_last_execution_id(session_id, request_id):
        persist_calls.append((session_id, request_id))

    async def _fake_execute_tool(*args, **kwargs):
        return ToolResult(success=True, content="ok", error=None)

    monkeypatch.setattr("src.sessions.manager.session_manager.set_last_execution_id", _fake_set_last_execution_id)
    monkeypatch.setattr("src.agents.core.execute_tool_by_name", _fake_execute_tool)

    result = await _execute_tool_via_runtime_bus(
        session_id="s-internal",
        tool_name="demo_tool",
        args={"k": "v"},
        runtime_config=None,
        source_ref="test",
    )

    assert result.success is True
    assert persist_calls == []
