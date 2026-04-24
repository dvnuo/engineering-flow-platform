from pathlib import Path


def test_jira_legacy_paths_do_not_use_synthetic_sessions():
    init_source = Path("src/jira/__init__.py").read_text(encoding="utf-8")
    api_source = Path("src/jira/api.py").read_text(encoding="utf-8")
    exporter_source = Path("src/jira/exporter.py").read_text(encoding="utf-8")

    assert 'session_id=f"jira-' not in init_source
    assert 'session_id=f"jira-' not in api_source
    assert 'or "unknown_session"' not in exporter_source
