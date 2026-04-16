import pytest


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_mention_passes_session_id_to_agent(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    calls = {"agent": 0, "comment": 0}

    async def _fake_process(*, message, session_id):
        calls["agent"] += 1
        assert session_id == "sess-1"
        assert "GitHub" in message
        return {"response": "ok"}

    async def _fake_add_comment(owner, repo, issue_number, body):
        calls["comment"] += 1
        assert owner == "octo"
        assert repo == "portal"
        assert issue_number == 2
        assert body == "ok"

    monkeypatch.setattr("src.runtime.triggered_event_task.agent.process", _fake_process)
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

    async def _fake_process(*, message, session_id):
        calls["agent"] += 1
        assert session_id == "sess-2"
        assert "Jira" in message
        return {"response": "looks good"}

    async def _fake_add_comment(issue_key, body):
        calls["comment"] += 1
        assert issue_key == "ENG-1"
        assert body == "looks good"

    monkeypatch.setattr("src.runtime.triggered_event_task.agent.process", _fake_process)
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
