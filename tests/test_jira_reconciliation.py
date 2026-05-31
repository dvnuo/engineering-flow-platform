import json
import asyncio
from pathlib import Path

import pytest
from tests._optional_runtime_deps import skip_if_missing_ruamel_yaml

skip_if_missing_ruamel_yaml("full runtime dependencies unavailable (missing ruamel.yaml)")



@pytest.mark.asyncio
async def test_jira_reconciliation_consumes_portal_contract_and_posts_ingress_payload(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()
    posts = []

    async def _fake_get_json(path, *, session=None):
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

    async def _fake_post_json(path, payload, *, session=None):
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
    monkeypatch.setattr("src.cron.jira_reconciliation.jira_cli.search_issues", _fake_search_issues)
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
async def test_jira_reconciliation_ignores_portal_self_service_metadata_and_posts_ingress(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()
    posts = []

    async def _fake_get_json(path, *, session=None):
        if path.endswith("workflow-transition-rules"):
            return {
                "items": [
                    {
                        "id": "r-self-service-1",
                        "system_type": "jira",
                        "is_enabled": True,
                        "project_key": "ENG",
                        "trigger_status": "In Progress",
                        "assignee_binding": "jira-acct-1",
                        "read_only": True,
                        "display_name": "Owner managed Jira workflow rule",
                        "ui_badges": ["self-service"],
                        "owner_user_id": 42,
                    }
                ]
            }
        if path.endswith("agent-identity-bindings"):
            return {"items": [{"agent_id": "a-1", "provider_type": "jira"}]}
        raise AssertionError("unexpected path")

    async def _fake_post_json(path, payload, *, session=None):
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
    monkeypatch.setattr("src.cron.jira_reconciliation.jira_cli.search_issues", _fake_search_issues)
    monkeypatch.setattr(runner, "_base_url", lambda: "https://portal.internal")

    await runner.reconcile_once()

    assert len(posts) == 1
    path, payload = posts[0]
    assert path == "/api/internal/external-events/ingest"
    assert payload["source_type"] == "jira"
    assert payload["event_type"] == "workflow_review_requested"
    assert payload["project_key"] == "ENG"
    assert payload["trigger_status"] == "In Progress"
    assert payload["issue_key"] == "ENG-1"
    parsed = json.loads(payload["payload_json"])
    assert parsed["mode"] == "reconciliation"
    assert parsed["workflow_rule_id"] == "r-self-service-1"


@pytest.mark.asyncio
async def test_jira_reconciliation_issue_failure_does_not_break_loop(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()
    posts = []

    async def _fake_get_json(path, *, session=None):
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

    async def _fake_post_json(path, payload, *, session=None):
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
    monkeypatch.setattr("src.cron.jira_reconciliation.jira_cli.search_issues", _fake_search_issues)
    monkeypatch.setattr(runner, "_base_url", lambda: "https://portal.internal")

    await runner.reconcile_once()

    assert len(posts) == 1
    assert posts[0][1]["issue_key"] == "ENG-2"


@pytest.mark.asyncio
async def test_jira_reconciliation_reuses_single_client_session_per_run(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()
    session_constructors = []
    sessions_seen = []
    posts = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _fake_client_session(*args, **kwargs):
        session = _FakeSession()
        session_constructors.append(session)
        return session

    async def _fake_get_json(path, *, session=None):
        sessions_seen.append(session)
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
        return {"items": [{"agent_id": "a-1", "provider_type": "jira"}]}

    async def _fake_post_json(path, payload, *, session=None):
        sessions_seen.append(session)
        posts.append((path, payload))

    async def _fake_search_issues(_jql, max_results=50):
        return {
            "issues": [
                {"key": "ENG-1", "fields": {"project": {"key": "ENG"}, "issuetype": {"name": "Bug"}, "status": {"name": "In Progress"}}},
                {"key": "ENG-2", "fields": {"project": {"key": "ENG"}, "issuetype": {"name": "Task"}, "status": {"name": "In Progress"}}},
            ]
        }

    monkeypatch.setattr("src.cron.jira_reconciliation.ClientSession", _fake_client_session)
    monkeypatch.setattr(runner, "_get_json", _fake_get_json)
    monkeypatch.setattr(runner, "_post_json", _fake_post_json)
    monkeypatch.setattr("src.cron.jira_reconciliation.jira_cli.search_issues", _fake_search_issues)
    monkeypatch.setattr(runner, "_base_url", lambda: "https://portal.internal")

    await runner.reconcile_once()

    assert len(session_constructors) == 1
    assert sessions_seen
    assert all(session is session_constructors[0] for session in sessions_seen)
    assert [payload["issue_key"] for _, payload in posts] == ["ENG-1", "ENG-2"]


@pytest.mark.asyncio
async def test_fetch_enabled_workflow_rules_accepts_bare_list_response(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()

    async def _fake_get_json(path, *, session=None):
        assert path == "/api/internal/workflow-transition-rules"
        return [
            {
                "id": "r-1",
                "system_type": "jira",
                "is_enabled": True,
                "project_key": "ENG",
                "trigger_status": "In Progress",
            }
        ]

    monkeypatch.setattr(runner, "_get_json", _fake_get_json)
    rules = await runner.fetch_enabled_workflow_rules()
    assert len(rules) == 1
    assert rules[0]["id"] == "r-1"
    assert rules[0]["provider_type"] == "jira"


@pytest.mark.asyncio
async def test_fetch_identity_bindings_accepts_bare_list_response(monkeypatch):
    from src.cron import jira_reconciliation

    runner = jira_reconciliation.JiraReconciliationRunner()

    async def _fake_get_json(path, *, session=None):
        assert path == "/api/internal/agent-identity-bindings"
        return [{"id": "b-1", "agent_id": "a-1", "provider_type": "jira"}]

    monkeypatch.setattr(runner, "_get_json", _fake_get_json)
    bindings = await runner.fetch_identity_bindings()
    assert bindings
    assert bindings[0]["id"] == "b-1"


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


def test_jira_reconciliation_default_disabled_in_example_config():
    source = Path("config.yaml.example").read_text(encoding="utf-8")
    assert "jira_reconciliation_enabled: false" in source


@pytest.mark.asyncio
async def test_shutdown_jira_reconciliation_task_cancels_and_swallows_cancelled_error(monkeypatch):
    import main as main_module

    class _FakeTask:
        def __init__(self):
            self.cancel_called = False

        def done(self):
            return False

        def cancel(self):
            self.cancel_called = True

        def __await__(self):
            async def _raise_cancelled():
                raise asyncio.CancelledError()

            return _raise_cancelled().__await__()

    calls = {"stop": 0}

    async def _fake_stop_reconciliation():
        calls["stop"] += 1

    monkeypatch.setattr(main_module, "stop_reconciliation", _fake_stop_reconciliation)

    fake_task = _FakeTask()
    await main_module._shutdown_jira_reconciliation_task(fake_task, main_module.logging.getLogger(__name__))

    assert calls["stop"] == 1
    assert fake_task.cancel_called is True
