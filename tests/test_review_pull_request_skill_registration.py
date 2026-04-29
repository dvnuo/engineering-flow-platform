from pathlib import Path


def test_review_pull_request_has_no_python_skill_executor_path():
    assert not Path("skills/review-pull-request/skill.py").exists()

    from src.agents.executor import list_available_skills

    assert "review-pull-request" not in list_available_skills()


def test_review_pull_request_markdown_skill_is_available():
    from src.skills import skill_registry

    if not skill_registry._initialized:
        skill_registry.load_skills()

    skill = skill_registry.get_skill("review-pull-request")
    assert skill is not None
    assert skill.name == "review-pull-request"
    assert "github_get_pr" in skill.tools
