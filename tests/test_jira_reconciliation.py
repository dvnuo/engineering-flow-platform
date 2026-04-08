import pytest


@pytest.mark.asyncio
async def test_jira_reconciliation_fetches_rules_and_posts_events(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()
    posts = []

    async def _fake_get_json(path):
        if path.endswith("workflow-transition-rules"):
            return {"items": [{"id": "r-1", "enabled": True, "provider_type": "jira", "project_keys": ["ENG"], "trigger_statuses": ["In Progress"]}]}
        if path.endswith("agent-identity-bindings"):
            return {"items": [{"agent_id": "a-1", "provider_type": "jira"}]}
        raise AssertionError("unexpected path")

    async def _fake_post_json(path, payload):
        posts.append((path, payload))

    async def _fake_search_issues(jql, max_results=50):
        assert "project IN (ENG)" in jql
        return {"issues": [{"key": "ENG-1", "fields": {"status": {"name": "In Progress"}}}]}

    monkeypatch.setattr(runner, "_get_json", _fake_get_json)
    monkeypatch.setattr(runner, "_post_json", _fake_post_json)
    monkeypatch.setattr("src.cron.jira_reconciliation.jira_channel.search_issues", _fake_search_issues)
    monkeypatch.setattr(runner, "_base_url", lambda: "https://portal.internal")

    await runner.reconcile_once()

    assert posts
    assert posts[0][0] == "/api/internal/external-events/ingest"
    payload = posts[0][1]
    assert payload["event_type"] == "jira.issue.updated"
    assert payload["payload"]["mode"] == "reconciliation"
    assert payload["payload"]["issue"]["key"] == "ENG-1"


@pytest.mark.asyncio
async def test_jira_reconciliation_issue_failure_does_not_break_loop(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()
    posts = []

    async def _fake_get_json(path):
        if path.endswith("workflow-transition-rules"):
            return {"items": [{"id": "r-1", "enabled": True, "provider_type": "jira", "project_keys": ["ENG"]}]}
        return {"items": []}

    async def _fake_post_json(path, payload):
        key = payload["payload"]["issue"]["key"]
        if key == "ENG-1":
            raise RuntimeError("boom")
        posts.append((path, payload))

    async def _fake_search_issues(_jql, max_results=50):
        return {"issues": [{"key": "ENG-1", "fields": {}}, {"key": "ENG-2", "fields": {}}]}

    monkeypatch.setattr(runner, "_get_json", _fake_get_json)
    monkeypatch.setattr(runner, "_post_json", _fake_post_json)
    monkeypatch.setattr("src.cron.jira_reconciliation.jira_channel.search_issues", _fake_search_issues)
    monkeypatch.setattr(runner, "_base_url", lambda: "https://portal.internal")

    await runner.reconcile_once()
    assert len(posts) == 1
    assert posts[0][1]["payload"]["issue"]["key"] == "ENG-2"


def test_jira_reconciliation_disabled_flag(monkeypatch):
    from src.cron import jira_reconciliation

    monkeypatch.setattr(jira_reconciliation.config, "get", lambda key, default=None: False if key == "server.jira_reconciliation_enabled" else default)
    assert jira_reconciliation.is_enabled() is False
