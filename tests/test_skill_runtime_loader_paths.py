import pytest

from src.agents.executor import SkillsExecutor
from src.gateway.server import Gateway


def test_executor_missing_skills_dir_does_not_raise(monkeypatch, tmp_path):
    missing = tmp_path / "missing-skills"
    monkeypatch.setenv("EFP_SKILLS_DIR", str(missing))
    SkillsExecutor()


def test_executor_loads_python_skill_from_env_dir(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "hello"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.py").write_text(
        """from src.agents.executor import skill\n\n@skill(name=\"hello\", description=\"hello\")\nasync def hello():\n    return \"ok\"\n""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EFP_SKILLS_DIR", str(skills_root))

    executor = SkillsExecutor()
    assert "hello" in executor.list_skills()


@pytest.mark.asyncio
async def test_skill_git_info_non_git_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("EFP_SKILLS_DIR", str(tmp_path))
    gw = Gateway()
    resp = await gw.handle_skill_git_info(None)
    payload = resp.text
    assert '"commit_id": null' in payload
    assert '"repo_url": null' in payload
