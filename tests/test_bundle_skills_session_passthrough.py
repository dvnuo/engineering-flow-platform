from pathlib import Path


def test_collect_requirements_skill_session_passthrough_regression():
    source = Path("skills/collect_requirements_to_bundle/skill.py").read_text(encoding="utf-8")
    assert "_session_id: str | None = None" in source
    assert "_load_jira_sources(jira_ids, session_id=_session_id)" in source
    assert "_load_confluence_sources(confluence_ids, session_id=_session_id)" in source
    assert "session_id=_session_id," in source
    assert "jira_get_issue(source, _session_id=session_id)" in source
    assert "confluence_get_page(source, _session_id=session_id)" in source


def test_collect_research_notes_skill_session_passthrough_regression():
    source = Path("skills/collect_research_notes_to_bundle/skill.py").read_text(encoding="utf-8")
    assert "_session_id: str | None = None" in source
    assert "_load_jira_sources(jira_ids, session_id=_session_id)" in source
    assert "_load_confluence_sources(confluence_ids, session_id=_session_id)" in source
    assert "session_id=_session_id," in source
