from pathlib import Path


def test_review_pull_request_has_no_python_skill_executor_path():
    assert not Path("skills/review-pull-request/skill.py").exists()

    from src.agents.executor import list_available_skills

    assert "review-pull-request" not in list_available_skills()


def test_review_pull_request_markdown_skill_is_available(tmp_path, monkeypatch):
    skill_dir = tmp_path / "review-pull-request"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text(
        """---
name: review-pull-request
description: Review pull requests
tools: [github_get_pr]
triggers: [/review-pr]
---
body
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("EFP_SKILLS_DIR", str(tmp_path))

    from src.skills.registry import SkillRegistry

    registry = SkillRegistry(project_skills_dir=None, user_skills_dir=tmp_path / "user")
    registry.load_skills()
    skill = registry.get_skill("review-pull-request")
    assert skill is not None
    assert skill.name == "review-pull-request"
    assert "github_get_pr" in skill.tools
