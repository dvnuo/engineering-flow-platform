"""Repo-relative skill paths, so Portal can link a slash command to its source.

Portal knows which skills repository and branch an assistant booted with, but
not where inside that checkout a given skill lives -- the directory name and the
declared skill name are allowed to differ. Without a path from the runtime it
would have to guess the directory, and guess wrong for any skill whose folder
is not spelled exactly like its name.
"""
from pathlib import Path

import pytest

from src.skills.registry import SkillRegistry


SKILL_BODY = """---
name: {name}
description: {description}
version: 1.0.0
owner: test
triggers: []
tools: []
---
body
"""


def _write_skill(root: Path, folder: str, *, name: str, description: str = "A skill") -> Path:
    skill_dir = root / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "skill.md"
    path.write_text(SKILL_BODY.format(name=name, description=description), encoding="utf-8")
    return path


def _registry(project: Path, user: Path) -> SkillRegistry:
    registry = SkillRegistry(project_skills_dir=project, user_skills_dir=user)
    registry.load_skills()
    return registry


def test_repo_path_is_relative_to_the_checkout_root(tmp_path):
    project = tmp_path / "skills"
    _write_skill(project, "create-pull-request", name="create-pull-request")

    summaries = _registry(project, tmp_path / "user").get_all_skill_summaries()

    assert [s["repo_path"] for s in summaries] == ["create-pull-request/skill.md"]


def test_repo_path_follows_the_directory_not_the_declared_name(tmp_path):
    # The folder and the skill name are allowed to disagree, which is exactly
    # why Portal cannot derive the path from the name it has.
    project = tmp_path / "skills"
    _write_skill(project, "design_test_cases_from_bundle", name="design-test-cases")

    summaries = _registry(project, tmp_path / "user").get_all_skill_summaries()

    assert summaries[0]["name"] == "design-test-cases"
    assert summaries[0]["repo_path"] == "design_test_cases_from_bundle/skill.md"


def test_repo_path_is_posix_so_a_windows_runtime_still_links_correctly(tmp_path):
    project = tmp_path / "skills"
    _write_skill(project, "nested", name="nested")

    repo_path = _registry(project, tmp_path / "user").get_all_skill_summaries()[0]["repo_path"]

    assert "\\" not in repo_path


def test_a_user_override_skill_gets_no_repo_path(tmp_path):
    # ~/.efp/skills is not part of the repository, so a link built from it
    # would 404. Better to have no link than a broken one.
    project = tmp_path / "skills"
    project.mkdir()
    user = tmp_path / "user-skills"
    _write_skill(user, "local-only", name="local-only")

    summaries = _registry(project, user).get_all_skill_summaries()

    assert [s["name"] for s in summaries] == ["local-only"]
    assert summaries[0]["repo_path"] == ""


def test_repo_path_never_leaks_the_container_filesystem(tmp_path):
    # An absolute path would expose the image layout to every browser that
    # loads the composer, and is useless as a repository link besides.
    project = tmp_path / "skills"
    _write_skill(project, "some-skill", name="some-skill")

    repo_path = _registry(project, tmp_path / "user").get_all_skill_summaries()[0]["repo_path"]

    assert not Path(repo_path).is_absolute()
    assert str(tmp_path) not in repo_path


@pytest.mark.parametrize("source_file", ["", None])
def test_a_skill_with_no_recorded_source_is_skipped_rather_than_raising(tmp_path, source_file):
    project = tmp_path / "skills"
    _write_skill(project, "some-skill", name="some-skill")
    registry = _registry(project, tmp_path / "user")
    registry.get_skill("some-skill").source_file = source_file

    assert registry.get_all_skill_summaries()[0]["repo_path"] == ""
