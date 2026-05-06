import json
from pathlib import Path

import pytest


def test_runtime_contract_fixture_files_exist():
    root = Path("tests/fixtures/runtime_contract")
    assert (root / "tools_repo/manifest.yaml").exists()
    assert (root / "tools_repo/tools/context/contract_echo.yaml").exists()
    assert (root / "tools_repo/python/efp_tools/runner.py").exists()
    assert (root / "skills_repo/smoke-skill/skill.md").exists()


def test_runtime_contract_fixture_external_tool_discovery(monkeypatch):
    monkeypatch.setenv("EFP_TOOLS_DIR", str(Path("tests/fixtures/runtime_contract/tools_repo").resolve()))
    from src.tools_external import get_external_tool_registry, reset_external_tool_registry_cache

    reset_external_tool_registry_cache()
    registry = get_external_tool_registry(force_reload=True)
    descriptor = registry.get_descriptor("contract_echo")
    assert descriptor is not None
    assert descriptor.tool_id == "efp.tool.contract.echo"
    assert descriptor.enabled is True
    assert "native" in descriptor.runtime_compat
    assert "opencode" in descriptor.runtime_compat
    assert descriptor.metadata.get("source") == "runtime_contract_fixture"


def test_runtime_contract_fixture_tool_schema_surface(monkeypatch):
    monkeypatch.setenv("EFP_TOOLS_DIR", str(Path("tests/fixtures/runtime_contract/tools_repo").resolve()))
    from src.tools_external import reset_external_tool_registry_cache

    reset_external_tool_registry_cache()
    from src import get_tools_schema

    schemas = get_tools_schema()
    names = {(item.get("function") or {}).get("name") for item in schemas if isinstance(item, dict)}
    assert "contract_echo" in names
    schema = next(item for item in schemas if (item.get("function") or {}).get("name") == "contract_echo")
    assert schema["metadata"]["tool_source"] == "external_tools_repo"
    assert schema["metadata"]["external_tool"] is True
    assert schema["metadata"]["mutation"] is False
    assert schema["metadata"]["risk_level"] == "low"


@pytest.mark.asyncio
async def test_runtime_contract_fixture_external_tool_executes(monkeypatch):
    monkeypatch.setenv("EFP_TOOLS_DIR", str(Path("tests/fixtures/runtime_contract/tools_repo").resolve()))
    from src.tools_external import reset_external_tool_registry_cache

    reset_external_tool_registry_cache()
    from src import execute_tool

    result = await execute_tool("contract_echo", text="hello", _session_id="s-contract", _runtime_type="native")
    assert result.success is True
    assert result.metadata["tool_source"] == "external_tools_repo"
    payload = json.loads(result.content)
    assert payload["tool"] == "contract_echo"
    assert payload["args"]["text"] == "hello"
    assert payload["runtime_type"] == "native"
    assert payload["session_id"] == "s-contract"
    assert payload["source"] == "runtime_contract_fixture"


def test_runtime_contract_fixture_skill_discovery(monkeypatch, tmp_path):
    monkeypatch.setenv("EFP_SKILLS_DIR", str(Path("tests/fixtures/runtime_contract/skills_repo").resolve()))
    from src.skills.registry import SkillRegistry

    registry = SkillRegistry(project_skills_dir=None, user_skills_dir=tmp_path / "user-skills")
    count = registry.load_skills()
    assert count >= 1
    skill = registry.get_skill("smoke-skill")
    assert skill is not None
    assert "contract_echo" in skill.tools


def test_runtime_contract_fixture_capability_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("EFP_TOOLS_DIR", str(Path("tests/fixtures/runtime_contract/tools_repo").resolve()))
    monkeypatch.setenv("EFP_SKILLS_DIR", str(Path("tests/fixtures/runtime_contract/skills_repo").resolve()))
    from src.tools_external import reset_external_tool_registry_cache

    reset_external_tool_registry_cache()
    from src.skills.registry import skill_registry

    skill_registry.project_skills_dir = Path("tests/fixtures/runtime_contract/skills_repo").resolve()
    skill_registry.user_skills_dir = tmp_path / "user-skills"
    skill_registry.skills.clear()
    skill_registry.load_skills()
    from src.runtime.capability_registry import build_default_capability_registry

    snapshot = build_default_capability_registry().export_catalog_snapshot()
    tools = [c for c in snapshot["capabilities"] if c.get("type") == "tool"]
    skills = [c for c in snapshot["capabilities"] if c.get("type") == "skill"]
    contract_tool = next(c for c in tools if c.get("name") == "contract_echo")
    assert contract_tool["capability_id"] == "efp.tool.contract.echo"
    assert contract_tool["metadata"]["tool_source"] == "external_tools_repo"
    assert contract_tool["metadata"]["descriptor_source_file"].endswith("contract_echo.yaml")
    assert any(c.get("name") == "smoke-skill" for c in skills)
