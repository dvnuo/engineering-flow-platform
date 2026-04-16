import json

import pytest


@pytest.mark.asyncio
async def test_run_once_uses_runtime_context_not_subscriptions(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()
    paths = []

    async def _fake_get_json(path, *, session=None):
        paths.append(path)
        if path == "/api/internal/agents/agent-1/runtime-context":
            return {
                "runtime_profile_context": {
                    "config": {
                        "github": {
                            "enabled": True,
                            "automation": {
                                "review_requests": {"enabled": True, "repos": ["acme/demo"]},
                                "mentions": {"enabled": True, "repos": ["acme/demo"]},
                            },
                        }
                    }
                }
            }
        if path == "/api/internal/agent-identity-bindings?enabled=true":
            return {
                "items": [
                    {
                        "id": "b1",
                        "agent_id": "agent-1",
                        "system_type": "github",
                        "username": "reviewer1",
                        "external_account_id": "gh-1",
                        "enabled": True,
                    }
                ]
            }
        raise AssertionError(path)

    polled = []

    async def _fake_poll(rule, *, session=None):
        polled.append(rule)

    monkeypatch.setattr("src.cron.subscription_watchers.is_portal_internal_configured", lambda: True)
    monkeypatch.setattr(manager, "_agent_id", lambda: "agent-1")
    monkeypatch.setattr(manager, "_get_json", _fake_get_json)
    monkeypatch.setattr(manager, "_poll_rule", _fake_poll)

    await manager.run_once()

    assert "/api/internal/agents/agent-1/runtime-context" in paths
    assert "/api/internal/agent-identity-bindings?enabled=true" in paths
    assert all("external-event-subscriptions" not in p for p in paths)
    assert {rule.source_kind for rule in polled} == {"github.pull_request_review_requested", "github.mention"}


def test_build_rules_scope_binding_overrides_profile_scope_when_non_empty():
    from src.cron.subscription_watchers import IdentityBinding, SubscriptionWatcherManager

    runtime_config = {
        "jira": {
            "enabled": True,
            "automation": {
                "assignments": {"enabled": True, "projects": ["PRJ"]},
                "mentions": {"enabled": True, "projects": ["PRJ"]},
            },
        }
    }
    bindings = [
        IdentityBinding(
            id="b-jira",
            agent_id="agent-1",
            system_type="jira",
            external_account_id="acct-1",
            username="jira-user",
            scope={"projects": ["OVERRIDE"]},
            enabled=True,
        )
    ]

    rules = SubscriptionWatcherManager.build_automation_rules(runtime_config, bindings, "agent-1")

    assert {r.source_kind for r in rules} == {"jira.assigned", "jira.mention"}
    assert all(r.scope["projects"] == ["OVERRIDE"] for r in rules)


@pytest.mark.asyncio
async def test_jira_assignment_poller_ingests_assigned_event(monkeypatch):
    from src.cron.subscription_watchers import AutomationRule, SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()
    posts = []

    async def _fake_search_issues(jql, max_results=50):
        assert "project IN (ENG)" in jql
        return {
            "issues": [
                {
                    "key": "ENG-7",
                    "fields": {
                        "summary": "Fix bug",
                        "updated": "2026-04-16T00:00:00.000+0000",
                        "project": {"key": "ENG"},
                        "status": {"name": "Todo"},
                        "assignee": {"accountId": "jira-acc-1"},
                    },
                    "self": "https://jira.local/rest/api/3/issue/ENG-7",
                }
            ]
        }

    async def _fake_post(path, payload, *, session=None):
        posts.append((path, payload))

    monkeypatch.setattr("src.cron.subscription_watchers.jira_channel.search_issues", _fake_search_issues)
    monkeypatch.setattr(manager, "_post_json", _fake_post)

    rule = AutomationRule(
        source_kind="jira.assigned",
        source_type="jira",
        event_type="assigned",
        scope={"projects": ["ENG"]},
        include_review_comments=False,
        binding_id="b-jira",
        binding_lookup_username="jira-user",
        external_account_id="jira-acc-1",
        automation_rule="jira.assignments",
    )

    await manager._poll_jira_assigned(rule)

    assert len(posts) == 1
    path, payload = posts[0]
    assert path == "/api/internal/external-events/ingest"
    assert payload["event_type"] == "assigned"
    assert payload["external_account_id"] == "jira-acc-1"


@pytest.mark.asyncio
async def test_github_mentions_include_review_comments(monkeypatch):
    from src.cron.subscription_watchers import AutomationRule, SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()
    posts = []

    async def _fake_recent_comments(repo_ref, since=None):
        return []

    async def _fake_search_issues(query, max_results=20):
        if "is:pr" in query:
            return {"items": [{"number": 12}]}
        return {"items": []}

    async def _fake_get_pr_comments(owner, repo, pull_number):
        return [
            {
                "id": 99,
                "body": "hello @agentuser",
                "url": "https://api.github.local/comment/99",
                "html_url": "https://github.local/acme/demo/pull/12#discussion_r99",
                "user": {"login": "alice"},
            }
        ]

    async def _fake_post(path, payload, *, session=None):
        posts.append((path, payload))

    monkeypatch.setattr("src.cron.subscription_watchers.github_channel.get_recent_issue_comments", _fake_recent_comments)
    monkeypatch.setattr("src.cron.subscription_watchers.github_channel.search_issues", _fake_search_issues)
    monkeypatch.setattr("src.cron.subscription_watchers.github_channel.get_pr_comments", _fake_get_pr_comments)
    monkeypatch.setattr(manager, "_post_json", _fake_post)

    rule = AutomationRule(
        source_kind="github.mention",
        source_type="github",
        event_type="mention",
        scope={"repos": ["acme/demo"]},
        include_review_comments=True,
        binding_id="b-gh",
        binding_lookup_username="agentuser",
        external_account_id="agentuser",
        automation_rule="github.mentions",
    )

    await manager._poll_github_mentions(rule)

    assert len(posts) == 1
    payload = posts[0][1]
    assert payload["event_type"] == "mention"
    payload_json = json.loads(payload["payload_json"])
    assert payload_json["comment_type"] == "review_comment"


@pytest.mark.asyncio
async def test_watcher_only_ingests_not_execute(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()

    async def _fake_get_json(path, *, session=None):
        if path == "/api/internal/agents/agent-1/runtime-context":
            return {
                "runtime_profile_context": {
                    "config": {
                        "github": {
                            "enabled": True,
                            "automation": {
                                "mentions": {"enabled": True, "repos": ["acme/demo"], "include_review_comments": False}
                            },
                        }
                    }
                }
            }
        if path == "/api/internal/agent-identity-bindings?enabled=true":
            return {
                "items": [
                    {
                        "id": "b1",
                        "agent_id": "agent-1",
                        "system_type": "github",
                        "username": "agentuser",
                        "external_account_id": "agentuser",
                    }
                ]
            }
        raise AssertionError(path)

    async def _fake_recent_comments(repo_ref, since=None):
        return [
            {
                "id": "c1",
                "body": "@agentuser ping",
                "author": "alice",
                "url": "https://github.local/acme/demo/issues/7#issuecomment-1",
                "issue_number": 7,
            }
        ]

    called = {"ingest": 0, "execute": 0}

    async def _fake_post(path, payload, *, session=None):
        called["ingest"] += 1

    async def _unexpected_execute(*args, **kwargs):
        called["execute"] += 1

    monkeypatch.setattr("src.cron.subscription_watchers.is_portal_internal_configured", lambda: True)
    monkeypatch.setattr(manager, "_agent_id", lambda: "agent-1")
    monkeypatch.setattr(manager, "_get_json", _fake_get_json)
    monkeypatch.setattr(manager, "_post_json", _fake_post)
    monkeypatch.setattr("src.cron.subscription_watchers.github_channel.get_recent_issue_comments", _fake_recent_comments)
    monkeypatch.setattr("src.runtime.execution_bus.execute_tool_by_name", _unexpected_execute)

    await manager.run_once()

    assert called["ingest"] == 1
    assert called["execute"] == 0


@pytest.mark.asyncio
async def test_fetch_identity_bindings_accepts_bare_list_response(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()

    async def _fake_get_json(path, *, session=None):
        assert path == "/api/internal/agent-identity-bindings?enabled=true"
        return [
            {
                "id": "b1",
                "agent_id": "agent-1",
                "system_type": "github",
                "username": "agentuser",
                "external_account_id": "agentuser",
            }
        ]

    monkeypatch.setattr(manager, "_get_json", _fake_get_json)
    bindings = await manager.fetch_identity_bindings()
    assert len(bindings) == 1
    assert bindings[0].id == "b1"
    assert bindings[0].system_type == "github"


@pytest.mark.asyncio
async def test_run_once_accepts_bare_list_bindings_response(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()
    polled = []

    async def _fake_get_json(path, *, session=None):
        if path == "/api/internal/agents/agent-1/runtime-context":
            return {
                "runtime_profile_context": {
                    "config": {
                        "github": {
                            "enabled": True,
                            "automation": {
                                "mentions": {"enabled": True, "repos": ["acme/demo"]},
                            },
                        }
                    }
                }
            }
        if path == "/api/internal/agent-identity-bindings?enabled=true":
            return [
                {
                    "id": "b1",
                    "agent_id": "agent-1",
                    "system_type": "github",
                    "username": "agentuser",
                    "external_account_id": "agentuser",
                    "enabled": True,
                }
            ]
        raise AssertionError(path)

    async def _fake_poll(rule, *, session=None):
        polled.append(rule)

    monkeypatch.setattr("src.cron.subscription_watchers.is_portal_internal_configured", lambda: True)
    monkeypatch.setattr(manager, "_agent_id", lambda: "agent-1")
    monkeypatch.setattr(manager, "_get_json", _fake_get_json)
    monkeypatch.setattr(manager, "_poll_rule", _fake_poll)

    await manager.run_once()

    assert polled
    assert any(rule.source_kind == "github.mention" for rule in polled)
