import json

import pytest


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
    bindings = [{"id": "b-1", "username": "reviewer1", "external_account_id": "ext-1"}]

    await manager._poll_github_review_requests(subscription, bindings)

    assert posts
    path, payload = posts[0]
    assert path == "/api/internal/external-events/ingest"
    assert payload["source_type"] == "github"
    assert payload["event_type"] == "pull_request_review_requested"
    assert payload["external_account_id"] == "reviewer1"
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


@pytest.mark.asyncio
async def test_build_mention_ingress_payloads_for_github_jira_confluence(monkeypatch):
    from src.cron.subscription_watchers import SubscriptionWatcherManager, WatchSubscription

    manager = SubscriptionWatcherManager()
    posts = []

    async def _fake_post_json(path, payload, *, session=None):
        posts.append(payload)

    monkeypatch.setattr(manager, "_post_json", _fake_post_json)

    bindings = [{"id": "b-1", "username": "reviewer1", "external_account_id": "reviewer1"}]

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

    await manager._poll_github_mentions(github_sub, bindings)
    await manager._poll_jira_mentions(jira_sub, bindings)
    await manager._poll_confluence_mentions(confluence_sub, bindings)

    assert any(p["dedupe_key"] == "github:mention:octo/portal:c1" for p in posts)
    assert any(p["dedupe_key"] == "jira:mention:ENG-1:jc1" for p in posts)
    assert any(p["dedupe_key"] == "confluence:mention:p1:cc1" for p in posts)


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
