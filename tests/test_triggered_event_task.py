import pytest


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_mention_passes_session_id_to_agent(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    calls = {"agent": 0, "comment": 0}

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage):
        calls["agent"] += 1
        assert session_id == "sess-1"
        assert "GitHub" in message
        assert user_name == "triggered-event"
        assert track_usage is False
        return {"response": "ok"}

    async def _fake_add_comment(owner, repo, issue_number, body):
        calls["comment"] += 1
        assert owner == "octo"
        assert repo == "portal"
        assert issue_number == 2
        assert body == "ok"

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task(
        {
            "source_kind": "github.mention",
            "session_id": "sess-1",
            "owner": "octo",
            "repo": "portal",
            "issue_number": 2,
            "body": "@agent hi",
        }
    )

    assert result["success"] is True
    assert calls["agent"] == 1
    assert calls["comment"] == 1


@pytest.mark.asyncio
async def test_run_triggered_event_task_jira_assigned_passes_session_id_to_agent(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    calls = {"agent": 0, "comment": 0}

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage):
        calls["agent"] += 1
        assert session_id == "sess-2"
        assert "Jira" in message
        assert user_name == "triggered-event"
        assert track_usage is False
        return {"response": "looks good"}

    async def _fake_add_comment(issue_key, body):
        calls["comment"] += 1
        assert issue_key == "ENG-1"
        assert body == "looks good"

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.jira_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task(
        {
            "source_kind": "jira.assigned",
            "session_id": "sess-2",
            "issue_key": "ENG-1",
            "summary": "Feature",
            "status": "Open",
            "assignee": "jira-user",
        }
    )

    assert result["success"] is True
    assert calls["agent"] == 1
    assert calls["comment"] == 1


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_mention_blocked_does_not_writeback(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    called = {"comment": 0}

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage):
        return {"response": "ok"}

    async def _fake_add_comment(*args, **kwargs):
        called["comment"] += 1

    def _blocked_gate(action_id, _kwargs):
        return {"blocked": True, "reason": "policy_blocked", "error": f"capability policy blocked for secondary action: {action_id}"}

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task(
        {
            "source_kind": "github.mention",
            "session_id": "sess-1",
            "owner": "octo",
            "repo": "portal",
            "issue_number": 2,
            "body": "@agent hi",
            "_action_gate": _blocked_gate,
        }
    )

    assert called["comment"] == 0
    assert result["success"] is False
    assert result["secondary_action_id"] == "adapter:github:add_comment"
    assert result["secondary_action_capability_type"] == "adapter_action"
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_run_triggered_event_task_jira_assigned_blocked_does_not_writeback(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    called = {"comment": 0}

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage):
        return {"response": "ok"}

    async def _fake_add_comment(*args, **kwargs):
        called["comment"] += 1

    def _blocked_gate(action_id, _kwargs):
        return {"blocked": True, "reason": "policy_blocked", "error": f"capability policy blocked for secondary action: {action_id}"}

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.jira_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task(
        {
            "source_kind": "jira.assigned",
            "session_id": "sess-2",
            "issue_key": "ENG-1",
            "summary": "Feature",
            "status": "Open",
            "assignee": "jira-user",
            "_action_gate": _blocked_gate,
        }
    )

    assert called["comment"] == 0
    assert result["success"] is False
    assert result["secondary_action_id"] == "adapter:jira:add_comment"
    assert result["secondary_action_capability_type"] == "adapter_action"
    assert result["blocked"] is True


@pytest.mark.asyncio
async def test_run_triggered_event_task_confluence_mention_blocked_does_not_writeback(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    called = {"comment": 0}

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage):
        return {"response": "ok"}

    async def _fake_add_comment(*args, **kwargs):
        called["comment"] += 1

    def _blocked_gate(action_id, _kwargs):
        return {"blocked": True, "reason": "policy_blocked", "error": f"capability policy blocked for secondary action: {action_id}"}

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.confluence_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task(
        {
            "source_kind": "confluence.mention",
            "session_id": "sess-3",
            "page_id": "1234",
            "title": "Doc",
            "space_key": "ENG",
            "body": "@agent hi",
            "_action_gate": _blocked_gate,
        }
    )

    assert called["comment"] == 0
    assert result["success"] is False
    assert result["secondary_action_id"] == "channel_action:confluence_add_comment"
    assert result["secondary_action_capability_type"] == "channel_action"
    assert result["blocked"] is True
