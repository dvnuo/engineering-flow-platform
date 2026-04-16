import json

import pytest
import asyncio
from pathlib import Path


@pytest.mark.asyncio
async def test_run_once_fetches_subscriptions_and_bindings_with_poll_filter(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()
    calls = []

    async def _fake_get_json(path, *, session=None):
        calls.append(path)
        if path.startswith("/api/internal/external-event-subscriptions"):
            return {
                "items": [
                    {
                        "id": "s-1",
                        "agent_id": "agent-1",
                        "source_type": "github",
                        "event_type": "pull_request_review_requested",
                        "mode": "poll",
                        "source_kind": "github.pull_request_review_requested",
                        "enabled": True,
                        "scope": {"repos": ["o/r"]},
                    },
                    {
                        "id": "s-2",
                        "agent_id": "agent-1",
                        "source_type": "github",
                        "event_type": "mention",
                        "mode": "push",
                        "source_kind": "github.mention",
                        "enabled": True,
                    },
                ]
            }
        if path == "/api/internal/agent-identity-bindings?enabled=true":
            return {
                "items": [
                    {
                        "id": "b-1",
                        "agent_id": "agent-1",
                        "system_type": "github",
                        "username": "reviewer1",
                    }
                ]
            }
        raise AssertionError("unexpected path")

    polled = []

    async def _fake_poll_subscription(subscription, bindings, *, session=None):
        polled.append((subscription, bindings, session))

    monkeypatch.setattr("src.cron.subscription_watchers.is_portal_internal_configured", lambda: True)
    monkeypatch.setattr(manager, "_get_json", _fake_get_json)
    monkeypatch.setattr(manager, "_agent_id", lambda: "agent-1")
    monkeypatch.setattr(manager, "_poll_subscription", _fake_poll_subscription)

    await manager.run_once()

    assert any(path.startswith("/api/internal/external-event-subscriptions") for path in calls)
    assert "/api/internal/agent-identity-bindings?enabled=true" in calls
    assert len(polled) == 1
    assert polled[0][0].id == "s-1"
    assert polled[0][1][0]["id"] == "b-1"


@pytest.mark.asyncio
async def test_run_once_normalizes_portal_self_service_export_shape(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()
    polled = []

    async def _fake_get_json(path, *, session=None):
        if path.startswith("/api/internal/external-event-subscriptions"):
            return {
                "items": [
                    {
                        "id": "s-gh-review",
                        "agent_id": "agent-1",
                        "source_type": "github",
                        "event_type": "pull_request_review_requested",
                        "mode": "poll",
                        "source_kind": "github.pull_request_review_requested",
                        "enabled": True,
                        "config_json": '{"allowed_repos": ["octo/portal"]}',
                        "scope_json": '{"repos": ["octo/portal"]}',
                        "matcher_json": '{"review_states": ["changes_requested"]}',
                        # Portal self-service control-plane/UI metadata should be ignored by runtime watcher.
                        "read_only": True,
                        "display_name": "Owner managed GitHub review watcher",
                    },
                    {
                        "id": "s-jira-mention",
                        "agent_id": "agent-1",
                        "provider_type": "jira",
                        "event_type": "mention",
                        "mode": "hybrid",
                        "source_kind": "jira.mention",
                        "enabled": True,
                        "target_ref": "ENG",
                        "routing_json": '{"queue":"external_events"}',
                        "poll_profile_json": '{"lookback_minutes": 90}',
                    },
                ]
            }
        if path == "/api/internal/agent-identity-bindings?enabled=true":
            return {
                "items": [
                    {"id": "b-gh", "agent_id": "agent-1", "system_type": "github", "username": "reviewer1"},
                    {"id": "b-jira", "agent_id": "agent-1", "provider_type": "jira", "username": "jira-user"},
                ]
            }
        raise AssertionError("unexpected path")

    async def _fake_poll_subscription(subscription, bindings, *, session=None):
        polled.append((subscription, bindings))

    monkeypatch.setattr("src.cron.subscription_watchers.is_portal_internal_configured", lambda: True)
    monkeypatch.setattr(manager, "_get_json", _fake_get_json)
    monkeypatch.setattr(manager, "_poll_subscription", _fake_poll_subscription)
    monkeypatch.setattr(manager, "_agent_id", lambda: "agent-1")

    await manager.run_once()

    assert len(polled) == 2

    github_subscription, github_bindings = polled[0]
    assert github_subscription.id == "s-gh-review"
    assert github_subscription.source_type == "github"
    assert github_subscription.scope == {"repos": ["octo/portal"]}
    assert github_subscription.matcher == {"review_states": ["changes_requested"]}
    assert github_subscription.routing == {}
    assert github_subscription.poll_profile == {}
    assert [b["id"] for b in github_bindings] == ["b-gh"]

    jira_subscription, jira_bindings = polled[1]
    assert jira_subscription.id == "s-jira-mention"
    assert jira_subscription.source_type == "jira"
    assert jira_subscription.routing == {"queue": "external_events"}
    assert jira_subscription.poll_profile == {"lookback_minutes": 90}
    assert jira_subscription.matcher == {}
    assert [b["id"] for b in jira_bindings] == ["b-jira"]


@pytest.mark.asyncio
async def test_run_once_self_service_github_mention_posts_ingest_not_direct_execute(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager
    import src.runtime.execution_bus as execution_bus

    manager = SubscriptionWatcherManager()
    posts = []

    async def _fake_get_json(path, *, session=None):
        if path.startswith("/api/internal/external-event-subscriptions"):
            return {
                "items": [
                    {
                        "id": "s-gh-mention",
                        "agent_id": "agent-1",
                        "provider_type": "github",
                        "event_type": "mention",
                        "mode": "poll",
                        "source_kind": "github.mention",
                        "enabled": True,
                        "target_ref": "octo/portal",
                        "read_only": True,
                        "display_name": "Owner managed mention watcher",
                        "ui_badges": ["self-service"],
                    }
                ]
            }
        if path == "/api/internal/agent-identity-bindings?enabled=true":
            return {
                "items": [
                    {
                        "id": "b-gh-1",
                        "agent_id": "agent-1",
                        "system_type": "github",
                        "username": "reviewer1",
                        "external_account_id": "gh-user-1",
                    }
                ]
            }
        raise AssertionError("unexpected path")

    async def _fake_get_recent_issue_comments(repo, since=None):
        assert repo == "octo/portal"
        return [
            {
                "id": "c1",
                "body": "ping @reviewer1",
                "author": "alice",
                "url": "https://github.com/octo/portal/issues/5#issuecomment-1",
                "issue_number": 5,
            }
        ]

    async def _fake_post_json(path, payload, *, session=None):
        posts.append((path, payload))

    async def _unexpected_execute(*args, **kwargs):
        raise AssertionError("run_once mention path must not call runtime direct execute entry")

    monkeypatch.setattr("src.cron.subscription_watchers.is_portal_internal_configured", lambda: True)
    monkeypatch.setattr(manager, "_get_json", _fake_get_json)
    monkeypatch.setattr(manager, "_post_json", _fake_post_json)
    monkeypatch.setattr(manager, "_agent_id", lambda: "agent-1")
    monkeypatch.setattr("src.cron.subscription_watchers.github_channel.get_recent_issue_comments", _fake_get_recent_issue_comments)
    if hasattr(execution_bus, "execute_tool_by_name"):
        monkeypatch.setattr(execution_bus, "execute_tool_by_name", _unexpected_execute)

    await manager.run_once()

    assert len(posts) == 1
    path, payload = posts[0]
    assert path == "/api/internal/external-events/ingest"
    assert payload["dedupe_key"] == "github:mention:octo/portal:c1"
    assert payload["external_account_id"] == "gh-user-1"

    metadata = json.loads(payload["metadata_json"])
    assert metadata["source_kind"] == "github.mention"
    assert metadata["subscription_id"] == "s-gh-mention"
    assert metadata["binding_id"] == "b-gh-1"
    assert metadata["subscription_mode"] == "poll"
    assert metadata["trigger_mode"] == "poll"


@pytest.mark.asyncio
async def test_poll_github_review_requests_posts_normalized_ingress(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager, WatchSubscription

    manager = SubscriptionWatcherManager()
    posts = []

    async def _fake_search_issues(query, max_results=50):
        assert query == "repo:octo/portal is:pr is:open review-requested:reviewer1"
        assert max_results == 50
        return {"items": [{"number": 123}]}

    async def _fake_get_pr(owner, repo, pull_number):
        assert owner == "octo"
        assert repo == "portal"
        assert pull_number == 123
        return {"head": {"sha": "abc123"}}

    async def _fake_post_json(path, payload, *, session=None):
        posts.append((path, payload))

    monkeypatch.setattr("src.cron.subscription_watchers.github_channel.search_issues", _fake_search_issues)
    monkeypatch.setattr("src.cron.subscription_watchers.github_channel.get_pull_request", _fake_get_pr)
    monkeypatch.setattr(manager, "_post_json", _fake_post_json)

    subscription = WatchSubscription(
        id="sub-1",
        agent_id="agent-1",
        source_type="github",
        event_type="pull_request_review_requested",
        mode="poll",
        source_kind="github.pull_request_review_requested",
        target_ref="octo/portal",
        binding_id=None,
        enabled=True,
        config={},
        scope={},
        matcher={},
        routing={},
        poll_profile={},
    )
    bindings = [{"id": "b-1", "username": "reviewer1", "external_account_id": "github-reviewer-123"}]

    await manager._poll_github_review_requests(subscription, bindings)

    assert posts
    path, payload = posts[0]
    assert path == "/api/internal/external-events/ingest"
    assert payload["source_type"] == "github"
    assert payload["event_type"] == "pull_request_review_requested"
    assert payload["external_account_id"] == "github-reviewer-123"
    assert payload["target_ref"] == "octo/portal"
    assert payload["dedupe_key"] == "github:review:octo/portal:123:reviewer1:abc123"
    parsed = json.loads(payload["payload_json"])
    assert parsed == {
        "owner": "octo",
        "repo": "portal",
        "pull_number": 123,
        "reviewer": "reviewer1",
        "head_sha": "abc123",
    }
    metadata = json.loads(payload["metadata_json"])
    assert metadata["trigger_mode"] == "poll"
    assert metadata["source_kind"] == "github.pull_request_review_requested"
    assert metadata["subscription_id"] == "sub-1"
    assert metadata["subscription_mode"] == "poll"
    assert metadata["binding_id"] == "b-1"
    assert metadata["binding_lookup_username"] == "reviewer1"
    assert metadata["reviewer_login"] == "reviewer1"


@pytest.mark.asyncio
async def test_build_mention_ingress_payloads_for_github_jira_confluence(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager, WatchSubscription

    manager = SubscriptionWatcherManager()
    posts = []

    async def _fake_post_json(path, payload, *, session=None):
        posts.append(payload)

    monkeypatch.setattr(manager, "_post_json", _fake_post_json)

    github_bindings = [{"id": "gh-bind-1", "username": "reviewer1", "external_account_id": "gh-user-1"}]
    jira_bindings = [{"id": "jira-bind-1", "username": "reviewer1", "external_account_id": "jira-account-1"}]
    confluence_bindings = [{"id": "conf-bind-1", "username": "reviewer1", "external_account_id": "conf-user-1"}]

    github_sub = WatchSubscription(
        id="gh-sub",
        agent_id="agent-1",
        source_type="github",
        event_type="mention",
        mode="poll",
        source_kind="github.mention",
        target_ref="octo/portal",
        binding_id=None,
        enabled=True,
        config={},
        scope={},
        matcher={},
        routing={},
        poll_profile={},
    )
    jira_sub = WatchSubscription(
        id="jira-sub",
        agent_id="agent-1",
        source_type="jira",
        event_type="mention",
        mode="poll",
        source_kind="jira.mention",
        target_ref="ENG",
        binding_id=None,
        enabled=True,
        config={},
        scope={},
        matcher={},
        routing={},
        poll_profile={},
    )
    confluence_sub = WatchSubscription(
        id="conf-sub",
        agent_id="agent-1",
        source_type="confluence",
        event_type="mention",
        mode="poll",
        source_kind="confluence.mention",
        target_ref="DEV",
        binding_id=None,
        enabled=True,
        config={},
        scope={},
        matcher={},
        routing={},
        poll_profile={},
    )

    async def _fake_get_recent_issue_comments(repo, since=None):
        return [{"id": "c1", "body": "hi @reviewer1", "author": "alice", "url": "https://github.com/octo/portal/issues/5#issuecomment-1", "issue_number": 5}]

    async def _fake_search_issues(jql, max_results=50):
        return {"issues": [{"key": "ENG-1", "fields": {"project": {"key": "ENG"}}}]}

    async def _fake_get_jira_comments(issue_key):
        return [{"id": "jc1", "body": "ping @reviewer1", "author": "bob"}]

    async def _fake_search_pages(cql, limit=20):
        return {"results": [{"id": "p1", "title": "Doc", "space": {"key": "DEV"}}]}

    async def _fake_get_conf_comments(page_id):
        return [{"id": "cc1", "body": {"storage": {"value": "<p>@reviewer1 please check</p>"}}, "version": {"by": {"displayName": "carol"}}}]

    monkeypatch.setattr("src.cron.subscription_watchers.github_channel.get_recent_issue_comments", _fake_get_recent_issue_comments)
    monkeypatch.setattr("src.cron.subscription_watchers.jira_channel.search_issues", _fake_search_issues)
    monkeypatch.setattr("src.cron.subscription_watchers.jira_channel.get_comments", _fake_get_jira_comments)
    monkeypatch.setattr("src.cron.subscription_watchers.confluence_channel.search_pages", _fake_search_pages)
    monkeypatch.setattr("src.cron.subscription_watchers.confluence_channel.get_comments", _fake_get_conf_comments)

    await manager._poll_github_mentions(github_sub, github_bindings)
    await manager._poll_jira_mentions(jira_sub, jira_bindings)
    await manager._poll_confluence_mentions(confluence_sub, confluence_bindings)

    github_payload = next(p for p in posts if p["dedupe_key"] == "github:mention:octo/portal:c1")
    jira_payload = next(p for p in posts if p["dedupe_key"] == "jira:mention:ENG-1:jc1")
    confluence_payload = next(p for p in posts if p["dedupe_key"] == "confluence:mention:p1:cc1")

    assert github_payload["external_account_id"] == "gh-user-1"
    assert jira_payload["external_account_id"] == "jira-account-1"
    assert confluence_payload["external_account_id"] == "conf-user-1"

    github_metadata = json.loads(github_payload["metadata_json"])
    assert github_metadata["trigger_mode"] == "poll"
    assert github_metadata["source_kind"] == "github.mention"
    assert github_metadata["subscription_id"] == "gh-sub"
    assert github_metadata["subscription_mode"] == "poll"
    assert github_metadata["binding_id"] == "gh-bind-1"
    assert github_metadata["binding_lookup_username"] == "reviewer1"

    jira_metadata = json.loads(jira_payload["metadata_json"])
    assert jira_metadata["trigger_mode"] == "poll"
    assert jira_metadata["source_kind"] == "jira.mention"
    assert jira_metadata["subscription_id"] == "jira-sub"
    assert jira_metadata["subscription_mode"] == "poll"
    assert jira_metadata["binding_id"] == "jira-bind-1"
    assert jira_metadata["binding_lookup_username"] == "reviewer1"

    confluence_metadata = json.loads(confluence_payload["metadata_json"])
    assert confluence_metadata["trigger_mode"] == "poll"
    assert confluence_metadata["source_kind"] == "confluence.mention"
    assert confluence_metadata["subscription_id"] == "conf-sub"
    assert confluence_metadata["subscription_mode"] == "poll"
    assert confluence_metadata["binding_id"] == "conf-bind-1"
    assert confluence_metadata["binding_lookup_username"] == "reviewer1"


@pytest.mark.asyncio
async def test_run_once_reuses_single_client_session_per_run(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()
    session_constructors = []
    sessions_seen = []

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
        if path.startswith("/api/internal/external-event-subscriptions"):
            return {
                "items": [
                    {
                        "id": "s-1",
                        "agent_id": "agent-1",
                        "source_type": "github",
                        "event_type": "pull_request_review_requested",
                        "mode": "poll",
                        "source_kind": "github.pull_request_review_requested",
                        "enabled": True,
                        "scope": {"repos": ["o/r"]},
                    }
                ]
            }
        return {
            "items": [
                {
                    "id": "b-1",
                    "agent_id": "agent-1",
                    "system_type": "github",
                    "username": "reviewer1",
                }
            ]
        }

    async def _fake_poll_subscription(subscription, bindings, *, session=None):
        sessions_seen.append(session)

    monkeypatch.setattr("src.cron.subscription_watchers.is_portal_internal_configured", lambda: True)
    monkeypatch.setattr("src.cron.subscription_watchers.ClientSession", _fake_client_session)
    monkeypatch.setattr(manager, "_get_json", _fake_get_json)
    monkeypatch.setattr(manager, "_poll_subscription", _fake_poll_subscription)
    monkeypatch.setattr(manager, "_agent_id", lambda: "agent-1")

    await manager.run_once()

    assert len(session_constructors) == 1
    assert sessions_seen
    assert all(session is session_constructors[0] for session in sessions_seen)


@pytest.mark.asyncio
async def test_stop_sets_event_and_allows_start_loop_to_exit_promptly(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()
    run_once_started = asyncio.Event()

    async def _fake_run_once():
        run_once_started.set()

    monkeypatch.setattr(manager, "run_once", _fake_run_once)
    monkeypatch.setattr("src.cron.subscription_watchers.get_interval_seconds", lambda: 999)

    start_task = asyncio.create_task(manager.start())
    await asyncio.wait_for(run_once_started.wait(), timeout=1.0)

    await manager.stop()
    await asyncio.wait_for(start_task, timeout=1.0)

    assert start_task.done()


@pytest.mark.asyncio
async def test_start_is_idempotent_when_already_running(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()
    run_once_calls = 0
    first_run_entered = asyncio.Event()

    async def _fake_run_once():
        nonlocal run_once_calls
        run_once_calls += 1
        first_run_entered.set()

    monkeypatch.setattr(manager, "run_once", _fake_run_once)
    monkeypatch.setattr("src.cron.subscription_watchers.get_interval_seconds", lambda: 999)

    first_start_task = asyncio.create_task(manager.start())
    await asyncio.wait_for(first_run_entered.wait(), timeout=1.0)
    calls_after_first_start = run_once_calls

    await asyncio.wait_for(manager.start(), timeout=1.0)
    await asyncio.sleep(0)

    assert run_once_calls == calls_after_first_start

    await manager.stop()
    await asyncio.wait_for(first_start_task, timeout=1.0)
    assert first_start_task.done()


@pytest.mark.asyncio
async def test_stop_is_safe_when_not_running():
    from src.cron.subscription_watchers import SubscriptionWatcherManager

    manager = SubscriptionWatcherManager()

    await manager.stop()

    assert manager._stop_event.is_set() is True


def test_subscription_watchers_does_not_call_execute_tool_directly():
    source = Path("src/cron/subscription_watchers.py").read_text(encoding="utf-8")
    assert "execute_tool(" not in source
