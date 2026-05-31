from pathlib import Path


def test_runtime_contract_fixture_files_exist():
    root = Path("tests/fixtures/runtime_contract")
    assert (root / "skills_repo/smoke-skill/skill.md").exists()


def test_runtime_contract_fixture_skill_discovery(monkeypatch, tmp_path):
    monkeypatch.setenv("EFP_SKILLS_DIR", str(Path("tests/fixtures/runtime_contract/skills_repo").resolve()))
    from src.skills.registry import SkillRegistry

    registry = SkillRegistry(project_skills_dir=None, user_skills_dir=tmp_path / "user-skills")
    count = registry.load_skills()
    assert count >= 1
    skill = registry.get_skill("smoke-skill")
    assert skill is not None


def test_runtime_contract_fixture_capability_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("EFP_SKILLS_DIR", str(Path("tests/fixtures/runtime_contract/skills_repo").resolve()))
    from src.skills.registry import skill_registry

    original_project_skills_dir = skill_registry.project_skills_dir
    original_user_skills_dir = skill_registry.user_skills_dir
    original_skills = dict(skill_registry.skills)
    original_initialized = getattr(skill_registry, "_initialized", False)

    try:
        skill_registry.project_skills_dir = Path("tests/fixtures/runtime_contract/skills_repo").resolve()
        skill_registry.user_skills_dir = tmp_path / "user-skills"
        skill_registry.skills.clear()
        skill_registry._initialized = False
        skill_registry.load_skills()

        from src.runtime.capability_registry import build_default_capability_registry

        snapshot = build_default_capability_registry().export_catalog_snapshot()
        tools = [c for c in snapshot["capabilities"] if c.get("type") == "tool"]
        skills = [c for c in snapshot["capabilities"] if c.get("type") == "skill"]
        assert tools
        assert all((c.get("metadata") or {}).get("tool_source") != "external_tools_repo" for c in tools)
        assert {"jira", "confluence", "browser"}.isdisjoint({c.get("name") for c in tools})
        assert any(c.get("name") == "smoke-skill" for c in skills)
    finally:
        skill_registry.project_skills_dir = original_project_skills_dir
        skill_registry.user_skills_dir = original_user_skills_dir
        skill_registry.skills.clear()
        skill_registry.skills.update(original_skills)
        skill_registry._initialized = original_initialized
