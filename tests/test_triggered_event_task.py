import pytest


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_mention_passes_session_id_to_agent(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    calls = {"agent": 0, "comment": 0}

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage, execution_metadata=None):
        calls["agent"] += 1
        assert session_id == "sess-1"
        assert "GitHub" in message
        assert user_name == "triggered-event"
        assert track_usage is False
        assert execution_metadata is None
        return {"response": "ok"}

    async def _fake_add_comment(owner, repo, issue_number, body):
        calls["comment"] += 1
        assert owner == "octo"
        assert repo == "portal"
        assert issue_number == 2
        assert "ok" in body
        assert "<!-- efp:auto-reply source=github-comment-mention" in body

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

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage, execution_metadata=None):
        calls["agent"] += 1
        assert session_id == "sess-2"
        assert "Jira" in message
        assert user_name == "triggered-event"
        assert track_usage is False
        assert execution_metadata is None
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

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage, execution_metadata=None):
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

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage, execution_metadata=None):
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

    async def _fake_run_chat_execution(*, agent, message, session_id, user_name, track_usage, execution_metadata=None):
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


@pytest.mark.asyncio
async def test_triggered_event_task_forwards_execution_metadata_to_agent(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    captured: dict = {}
    metadata = {
        "allowed_capability_ids": ["tool:jira_search"],
        "llm_tool_loop": {
            "one_tool_per_turn": True,
            "parallel_tool_calls": False,
            "max_repeated_tool_signature": 2,
        },
    }

    async def _fake_run_chat_execution(
        *,
        agent,
        message,
        session_id,
        user_name,
        track_usage,
        execution_metadata=None,
    ):
        captured["execution_metadata"] = execution_metadata
        return {"response": "ok"}

    async def _fake_add_comment(owner, repo, issue_number, body):
        return None

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task(
        {
            "source_kind": "github.mention",
            "session_id": "test-session",
            "owner": "octo",
            "repo": "repo",
            "issue_number": 123,
            "body": "@agent please investigate",
            "_execution_metadata": metadata,
            "_action_gate": lambda action_id, kwargs: {"blocked": False},
        }
    )

    assert result["success"] is True
    assert captured["execution_metadata"] == metadata


def test_triggered_event_execution_bus_preserves_execution_metadata_source_guard():
    import inspect
    import src.runtime.execution_bus as execution_bus

    source = inspect.getsource(execution_bus)
    assert '"_execution_metadata"' in source
    assert "dict(metadata)" in source


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_issue_comment_appends_marker(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    captured = {}

    async def _fake_run_chat_execution(**_kwargs):
        return {"response": "ok"}

    async def _fake_add_comment(owner, repo, issue_number, body):
        captured.update({"owner": owner, "repo": repo, "issue_number": issue_number, "body": body})

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task({"source_kind":"github.mention","session_id":"s1","owner":"octo","repo":"portal","issue_number":2,"comment_id":9,"comment_kind":"issue_comment","automation_rule_id":"r1","body":"@agent hi"})
    assert "ok" in captured["body"]
    assert "<!-- efp:auto-reply source=github-comment-mention" in captured["body"]
    assert result["secondary_action_id"] == "adapter:github:add_comment"


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_review_comment_replies_same_surface(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    called = {"add": 0}
    captured = {}

    async def _fake_run_chat_execution(**_kwargs):
        return {"response": "reply"}

    async def _fake_reply(owner, repo, pull_number, comment_id, body):
        captured.update({"owner": owner, "repo": repo, "pull_number": pull_number, "comment_id": comment_id, "body": body})

    async def _fake_add_comment(*args, **kwargs):
        called["add"] += 1

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.reply_pr_review_comment", _fake_reply)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task({"source_kind":"github.mention","session_id":"s1","comment_kind":"pull_request_review_comment","reply_mode":"same_surface","owner":"octo","repo":"portal","pull_number":7,"comment_id":100,"in_reply_to_id":99,"body":"@agent check","path":"src/a.py","line":10,"diff_hunk":"@@"})
    assert captured["comment_id"] == 99
    assert result["secondary_action_id"] == "adapter:github:reply_review_comment"
    assert called["add"] == 0


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_review_comment_timeline_fallback(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    captured = {}

    async def _fake_run_chat_execution(**_kwargs):
        return {"response": "reply"}

    async def _fake_add_comment(owner, repo, issue_number, body):
        captured.update({"owner": owner, "repo": repo, "issue_number": issue_number, "body": body})

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task({"source_kind":"github.mention","session_id":"s1","comment_kind":"pull_request_review_comment","reply_mode":"timeline","owner":"octo","repo":"portal","pull_number":7,"comment_id":100,"body":"@agent check"})
    assert captured["issue_number"] == 7
    assert result["secondary_action_id"] == "adapter:github:add_comment"


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_review_comment_blocked_does_not_writeback(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    called = {"reply": 0, "add": 0}

    async def _fake_run_chat_execution(**_kwargs):
        return {"response": "reply"}

    async def _fake_reply(*args, **kwargs):
        called["reply"] += 1

    async def _fake_add_comment(*args, **kwargs):
        called["add"] += 1

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.reply_pr_review_comment", _fake_reply)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)

    result = await run_triggered_event_task({"source_kind":"github.mention","session_id":"s1","comment_kind":"pull_request_review_comment","reply_mode":"same_surface","owner":"octo","repo":"portal","pull_number":7,"comment_id":100,"body":"@agent check","_action_gate":lambda action_id,_kwargs:{"blocked":True,"reason":"policy"}})
    assert result["blocked"] is True
    assert called["reply"] == 0
    assert called["add"] == 0


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_unsupported_comment_kind_fails(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    called = {"reply": 0, "add": 0}

    async def _fake_run_chat_execution(**_kwargs):
        return {"response": "reply"}

    async def _fake_reply(*args, **kwargs):
        called["reply"] += 1

    async def _fake_add_comment(*args, **kwargs):
        called["add"] += 1

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.reply_pr_review_comment", _fake_reply)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _fake_add_comment)

    with pytest.raises(ValueError, match="Unsupported GitHub mention comment_kind"):
        await run_triggered_event_task({"source_kind":"github.mention","session_id":"s1","comment_kind":"discussion_comment","owner":"octo","repo":"portal","comment_id":100,"body":"@agent check"})

    assert called["reply"] == 0
    assert called["add"] == 0


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_unsupported_reply_mode_fails(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    async def _should_not_run_chat(**_kwargs):
        raise AssertionError("run_chat_execution should not be called for invalid reply_mode")

    async def _should_not_add_comment(*args, **kwargs):
        raise AssertionError("add_comment should not be called for invalid reply_mode")

    async def _should_not_reply_comment(*args, **kwargs):
        raise AssertionError("reply_pr_review_comment should not be called for invalid reply_mode")

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _should_not_run_chat)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _should_not_add_comment)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.reply_pr_review_comment", _should_not_reply_comment)

    with pytest.raises(ValueError, match="Unsupported GitHub mention reply_mode"):
        await run_triggered_event_task({"source_kind":"github.mention","session_id":"s1","comment_kind":"issue_comment","reply_mode":"foo","owner":"octo","repo":"portal","issue_number":1,"comment_id":100,"body":"@agent check"})


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_commit_comment_adds_commit_comment(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    captured = {}

    async def _fake_run_chat_execution(**_kwargs):
        return {"response": "ok"}

    async def _fake_add_commit_comment(owner, repo, commit_sha, body, path=None, line=None, position=None):
        captured.update({"owner": owner, "repo": repo, "commit_sha": commit_sha, "body": body, "path": path, "line": line, "position": position})

    async def _should_not_add_comment(*args, **kwargs):
        raise AssertionError("add_comment should not be called")

    async def _should_not_reply(*args, **kwargs):
        raise AssertionError("reply_pr_review_comment should not be called")

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_commit_comment", _fake_add_commit_comment)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_comment", _should_not_add_comment)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.reply_pr_review_comment", _should_not_reply)

    result = await run_triggered_event_task({"source_kind":"github.mention","session_id":"s1","comment_kind":"commit_comment","reply_mode":"same_surface","owner":"octo","repo":"portal","commit_id":"abc123","commit_sha":"abc123","comment_id":100,"body":"@agent check","path":"src/a.py","line":10,"position":5})
    assert result["secondary_action_id"] == "adapter:github:add_commit_comment"
    assert captured["commit_sha"] == "abc123"
    assert "<!-- efp:auto-reply source=github-comment-mention" in captured["body"]


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_commit_comment_blocked_does_not_writeback(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    called = {"commit": 0}

    async def _fake_run_chat_execution(**_kwargs):
        return {"response": "ok"}

    async def _fake_add_commit_comment(*args, **kwargs):
        called["commit"] += 1

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr("src.runtime.triggered_event_task.github_channel.add_commit_comment", _fake_add_commit_comment)

    result = await run_triggered_event_task({"source_kind":"github.mention","session_id":"s1","comment_kind":"commit_comment","reply_mode":"same_surface","owner":"octo","repo":"portal","commit_sha":"abc123","comment_id":100,"body":"@agent check","_action_gate":lambda action_id,_kwargs:{"blocked":True,"reason":"policy"}})
    assert result["blocked"] is True
    assert called["commit"] == 0


@pytest.mark.asyncio
async def test_run_triggered_event_task_github_discussion_comment_still_unsupported(monkeypatch):
    from src.runtime.triggered_event_task import run_triggered_event_task

    async def _should_not_run_chat(**_kwargs):
        raise AssertionError("run_chat_execution should not be called")

    monkeypatch.setattr("src.runtime.triggered_event_task.run_chat_execution", _should_not_run_chat)

    with pytest.raises(ValueError, match="Unsupported GitHub mention comment_kind"):
        await run_triggered_event_task({"source_kind":"github.mention","session_id":"s1","comment_kind":"discussion_comment","reply_mode":"same_surface","owner":"octo","repo":"portal","comment_id":100,"body":"@agent check"})
