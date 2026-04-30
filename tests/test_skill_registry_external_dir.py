from pathlib import Path

from src.skills.registry import SkillRegistry


def test_skill_registry_loads_from_external_env_dir(tmp_path, monkeypatch):
    skill_dir = tmp_path / "review-pull-request"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text(
        """---
name: review-pull-request
description: Review pull requests
version: 1.0.0
owner: test
triggers:
  - /review-pull-request
tools: []
---
body
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("EFP_SKILLS_DIR", str(tmp_path))
    registry = SkillRegistry(project_skills_dir=None, user_skills_dir=tmp_path / "user-skills")

    assert registry.load_skills() == 1
    skill = registry.get_skill("review-pull-request")
    assert skill is not None
    assert skill.path == str(Path(tmp_path) / "review-pull-request")
