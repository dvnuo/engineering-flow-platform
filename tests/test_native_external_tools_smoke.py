import json

import pytest


@pytest.fixture
def native_external_fixture(tmp_path, monkeypatch):
    tools_repo = tmp_path / "tools_repo"
    (tools_repo / "tools" / "context").mkdir(parents=True)
    (tools_repo / "tools" / "git").mkdir(parents=True)
    (tools_repo / "python" / "efp_tools").mkdir(parents=True)

    (tools_repo / "manifest.yaml").write_text(
        'repo: engineering-flow-platform-tools\nversion: "0.1.0"\nschema_version: t04\n',
        encoding="utf-8",
    )
    (tools_repo / "tools" / "context" / "context_echo.yaml").write_text(
        """
name: context_echo
description: External context echo
python_entrypoint: ignored.module:ignored
enabled: true
runtime_compat: [native, opencode]
metadata:
  source: fixture
input_schema:
  type: object
  properties:
    text:
      type: string
""",
        encoding="utf-8",
    )
    (tools_repo / "tools" / "context" / "context_read_ref.yaml").write_text(
        """
name: context_read_ref
description: External context read ref override
python_entrypoint: ignored.module:ignored
enabled: true
allow_override: true
runtime_compat: [native, opencode]
risk_level: low
mutation: false
policy_tags: [context, read_only]
input_schema:
  type: object
  properties:
    ref:
      type: string
""",
        encoding="utf-8",
    )
    (tools_repo / "tools" / "git" / "git_status.yaml").write_text(
        """
name: git_status
description: External git status should be shadowed
python_entrypoint: ignored.module:ignored
enabled: true
runtime_compat: [native, opencode]
""",
        encoding="utf-8",
    )
    (tools_repo / "python" / "efp_tools" / "__init__.py").write_text("", encoding="utf-8")
    (tools_repo / "python" / "efp_tools" / "runner.py").write_text(
        """
import json

async def execute_tool_async(*, tools_dir, tool, args=None, context=None):
    return {
        "success": True,
        "content": json.dumps({
            "tool": tool,
            "args": args or {},
            "runtime_type": (context or {}).get("runtime_type"),
            "source": "external_runner",
        }),
    }
""",
        encoding="utf-8",
    )

    skills_repo = tmp_path / "skills_repo"
    (skills_repo / "smoke-skill").mkdir(parents=True)
    (skills_repo / "smoke-skill" / "skill.md").write_text(
        """---
name: smoke-skill
description: Native smoke skill
version: 1.0.0
owner: test
triggers:
  - /smoke-skill
tools:
  - context_echo
  - context_read_ref
---
Smoke body.
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("EFP_TOOLS_DIR", str(tools_repo))
    monkeypatch.setenv("EFP_SKILLS_DIR", str(skills_repo))
    from src.tools_external import reset_external_tool_registry_cache

    reset_external_tool_registry_cache()
    return tmp_path


def _schema_name(schema):
    return (schema.get("function") or {}).get("name")


@pytest.mark.asyncio
async def test_native_external_tools_smoke_schema_execution_and_capability(native_external_fixture):
    from src import execute_tool, get_tools_schema
    from src.runtime.capability_registry import build_default_capability_registry

    schemas = get_tools_schema()
    names = {_schema_name(item) for item in schemas if isinstance(item, dict)}
    assert "context_echo" in names

    context_read_ref_schema = next(item for item in schemas if _schema_name(item) == "context_read_ref")
    assert context_read_ref_schema["function"]["description"] == "External context read ref override"

    git_status_schema = next(item for item in schemas if _schema_name(item) == "git_status")
    assert git_status_schema["function"]["description"] != "External git status should be shadowed"

    echo_result = await execute_tool("context_echo", text="hello")
    assert echo_result.success is True
    assert echo_result.to_dict()["metadata"]["tool_source"] == "external_tools_repo"

    read_ref_result = await execute_tool("context_read_ref", ref="ctx://x")
    assert read_ref_result.success is True
    payload = json.loads(read_ref_result.content)
    assert payload["source"] == "external_runner"
    assert read_ref_result.to_dict()["metadata"]["external_override"] is True

    registry = build_default_capability_registry()
    caps = {cap.name: cap for cap in registry.list_by_type("tool")}
    assert caps["context_echo"].metadata["tool_source"] == "external_tools_repo"
    assert caps["context_echo"].metadata["external_override"] is False
    assert caps["context_read_ref"].metadata["tool_source"] == "external_tools_repo"
    assert caps["context_read_ref"].metadata["external_override"] is True
    assert caps["git_status"].metadata["tool_source"] == "legacy_builtin"
    assert caps["git_status"].metadata["external_shadowed_by_legacy"] is True
    assert caps["git_status"].metadata["external_shadow_reason"] == "allow_override_not_enabled"


def test_native_external_skills_smoke_loads_from_env_dir(native_external_fixture, tmp_path):
    from src.skills.registry import SkillRegistry

    registry = SkillRegistry(project_skills_dir=None, user_skills_dir=tmp_path / "user-skills")
    assert registry.load_skills() == 1
    skill = registry.get_skill("smoke-skill")
    assert skill is not None
    assert skill.tools == ["context_echo", "context_read_ref"]
