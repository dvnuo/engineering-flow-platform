from pathlib import Path


def test_src_readme_does_not_describe_repo_root_skills_as_default():
    readme = Path("src/README.md").read_text(encoding="utf-8")
    assert "Skills in `skills/`" not in readme


def test_registry_has_no_skill_yaml_terminology():
    registry_src = Path("src/skills/registry.py").read_text(encoding="utf-8")
    assert "skill.yaml" not in registry_src


def test_no_direct_external_collect_skill_imports_in_tests():
    tests_dir = Path("tests")
    offenders = []
    for test_file in tests_dir.rglob("test_*.py"):
        text = test_file.read_text(encoding="utf-8")
        marker = "from skills." + "collect_"
        if marker in text:
            offenders.append(str(test_file))
    assert offenders == []


def test_no_reading_real_repo_root_skills_files_from_tests():
    tests_dir = Path("tests")
    offenders = []
    patterns = (
        'Path("skills/',
        "Path('skills/",
    )
    for test_file in tests_dir.rglob("test_*.py"):
        text = test_file.read_text(encoding="utf-8")
        if not any(pattern in text for pattern in patterns):
            continue

        lines = text.splitlines()
        for idx, line in enumerate(lines, start=1):
            if 'Path("skills/' not in line and "Path('skills/" not in line:
                continue
            lower = line.lower()
            if any(token in lower for token in ("read_text", "open(", "spec_from_file_location", "import_module", "__import__")):
                offenders.append(f"{test_file}:{idx}")
    assert offenders == []


def test_root_readme_has_no_legacy_top_level_skills_description():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "Skill definitions (Markdown + code)" not in readme


def test_workspace_agents_example_uses_lowercase_skill_md():
    text = Path("workspace/AGENTS.md.example").read_text(encoding="utf-8")
    assert "SKILL.md" not in text


def test_refactor_guide_skill_md_mentions_are_marked_historical():
    text = Path("docs/REFACTOR_GUIDE.md").read_text(encoding="utf-8")
    if "SKILL.md" in text:
        lower = text.lower()
        assert ("historical" in lower) or ("legacy" in lower) or ("predates the external engineering-flow-platform-skills repository" in text)
