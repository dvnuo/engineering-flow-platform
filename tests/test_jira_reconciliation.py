import json

import pytest


@pytest.mark.asyncio
async def test_jira_reconciliation_consumes_portal_contract_and_posts_ingress_payload(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()
    posts = []

    async def _fake_get_json(path):
        if path.endswith("workflow-transition-rules"):
            return {
                "items": [
                    {
                        "id": "r-1",
                        "system_type": "jira",
                        "is_enabled": True,
                        "project_key": "ENG",
                        "trigger_status": "In Progress",
                        "assignee_binding": "jira-acct-1",
                    }
                ]
            }
        if path.endswith("agent-identity-bindings"):
            return {"items": [{"agent_id": "a-1", "provider_type": "jira"}]}
        raise AssertionError("unexpected path")

    async def _fake_post_json(path, payload):
        posts.append((path, payload))

    async def _fake_search_issues(jql, max_results=50):
        assert 'project IN (ENG)' in jql
        assert 'status IN ("In Progress")' in jql
        return {
            "issues": [
                {
                    "key": "ENG-1",
                    "fields": {
                        "project": {"key": "ENG"},
                        "issuetype": {"name": "Story"},
                        "status": {"name": "In Progress"},
                        "assignee": {"accountId": "jira-acct-1", "displayName": "User One"},
                    },
                }
            ]
        }

    monkeypatch.setattr(runner, "_get_json", _fake_get_json)
    monkeypatch.setattr(runner, "_post_json", _fake_post_json)
    monkeypatch.setattr("src.cron.jira_reconciliation.jira_channel.search_issues", _fake_search_issues)
    monkeypatch.setattr(runner, "_base_url", lambda: "https://portal.internal")

    await runner.reconcile_once()

    assert posts
    assert posts[0][0] == "/api/internal/external-events/ingest"
    payload = posts[0][1]
    assert payload["source_type"] == "jira"
    assert payload["event_type"] == "workflow_review_requested"
    assert payload["project_key"] == "ENG"
    assert payload["issue_type"] == "Story"
    assert payload["trigger_status"] == "In Progress"
    assert payload["issue_key"] == "ENG-1"
    assert payload["issue_assignee"] == "jira-acct-1"
    assert isinstance(payload.get("payload_json"), str)
    parsed = json.loads(payload["payload_json"])
    assert parsed["mode"] == "reconciliation"
    assert parsed["workflow_rule_id"] == "r-1"


@pytest.mark.asyncio
async def test_jira_reconciliation_issue_failure_does_not_break_loop(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()
    posts = []

    async def _fake_get_json(path):
        if path.endswith("workflow-transition-rules"):
            return {
                "items": [
                    {
                        "id": "r-1",
                        "system_type": "jira",
                        "is_enabled": True,
                        "project_key": "ENG",
                        "trigger_status": "In Progress",
                    }
                ]
            }
        return {"items": []}

    async def _fake_post_json(path, payload):
        if payload["issue_key"] == "ENG-1":
            raise RuntimeError("boom")
        posts.append((path, payload))

    async def _fake_search_issues(_jql, max_results=50):
        return {
            "issues": [
                {"key": "ENG-1", "fields": {"project": {"key": "ENG"}, "issuetype": {"name": "Bug"}, "status": {"name": "In Progress"}}},
                {"key": "ENG-2", "fields": {"project": {"key": "ENG"}, "issuetype": {"name": "Task"}, "status": {"name": "In Progress"}}},
            ]
        }

    monkeypatch.setattr(runner, "_get_json", _fake_get_json)
    monkeypatch.setattr(runner, "_post_json", _fake_post_json)
    monkeypatch.setattr("src.cron.jira_reconciliation.jira_channel.search_issues", _fake_search_issues)
    monkeypatch.setattr(runner, "_base_url", lambda: "https://portal.internal")

    await runner.reconcile_once()

    assert len(posts) == 1
    assert posts[0][1]["issue_key"] == "ENG-2"


def test_build_external_event_ingress_request_shape():
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()
    payload = runner.build_external_event_ingress_request(
        rule={
            "id": "r-7",
            "project_keys": ["ENG"],
            "trigger_statuses": ["In Progress"],
            "assignee_binding": "acct-77",
        },
        issue={
            "key": "ENG-77",
            "fields": {
                "project": {"key": "ENG"},
                "issuetype": {"name": "Story"},
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "Alice"},
            },
        },
        identity_bindings=[],
    )

    assert payload is not None
    assert payload["event_type"] == "workflow_review_requested"
    assert "payload_json" in payload
    assert "project_key" in payload
    assert "issue_type" in payload
    assert "trigger_status" in payload
    assert "issue_key" in payload
    assert "event_key" not in payload
    assert "payload" not in payload


def test_jira_reconciliation_disabled_flag(monkeypatch):
    from src.cron import jira_reconciliation

    monkeypatch.setattr(jira_reconciliation.config, "get", lambda key, default=None: False if key == "server.jira_reconciliation_enabled" else default)
    assert jira_reconciliation.is_enabled() is False
