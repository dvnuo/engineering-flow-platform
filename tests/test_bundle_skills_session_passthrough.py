from pathlib import Path


def _write_skill_py(tmp_path: Path, skill_name: str) -> Path:
    skill_dir = tmp_path / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "skill.py"
    skill_file.write_text(
        """
_session_id: str | None = None

async def load(jira_ids, confluence_ids, session_id=None):
    _load_jira_sources(jira_ids, session_id=_session_id)
    _load_confluence_sources(confluence_ids, session_id=_session_id)
    prepare_jira_issue_source("A-1", session_id=session_id)
    prepare_confluence_page_source("1", session_id=session_id)
""",
        encoding="utf-8",
    )
    return skill_file


def test_collect_requirements_skill_session_passthrough_regression(tmp_path):
    source = _write_skill_py(tmp_path, "collect_requirements_to_bundle").read_text(encoding="utf-8")
    assert "_session_id: str | None = None" in source
    assert "_load_jira_sources(jira_ids, session_id=_session_id)" in source
    assert "_load_confluence_sources(confluence_ids, session_id=_session_id)" in source
    assert "session_id=_session_id" in source
    assert "prepare_jira_issue_source" in source
    assert "prepare_confluence_page_source" in source


def test_collect_research_notes_skill_session_passthrough_regression(tmp_path):
    source = _write_skill_py(tmp_path, "collect_research_notes_to_bundle").read_text(encoding="utf-8")
    assert "_session_id: str | None = None" in source
    assert "_load_jira_sources(jira_ids, session_id=_session_id)" in source
    assert "_load_confluence_sources(confluence_ids, session_id=_session_id)" in source
    assert "session_id=_session_id" in source
