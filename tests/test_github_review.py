import pytest


class _SkillResult:
    def __init__(self, success=True, output="", error=None, data=None):
        self.success = success
        self.output = output
        self.error = error
        self.data = data or {}


@pytest.mark.asyncio
async def test_github_review_task_maps_approved_to_approve_event(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(success=True, output="Looks good", data={"approved": True})

    captured = {}

    async def _fake_execute_adapter_action(action_id, kwargs):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 1}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review.execute_skill", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action", _fake_execute_adapter_action)

    result = await run_github_review_task({"owner": "acme", "repo": "demo", "pull_number": 1})

    assert result["success"] is True
    assert captured["action_id"] == "adapter:github:review_pull_request"
    assert captured["kwargs"]["review_event"] == "APPROVE"
    assert result["review_event"] == "APPROVE"


@pytest.mark.asyncio
async def test_github_review_task_maps_rejected_to_request_changes_event(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(success=True, output="Need changes", data={"approved": False})

    captured = {}

    async def _fake_execute_adapter_action(action_id, kwargs):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 2}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review.execute_skill", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action", _fake_execute_adapter_action)

    result = await run_github_review_task({"owner": "acme", "repo": "demo", "pull_number": 2})

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "REQUEST_CHANGES"
    assert result["review_event"] == "REQUEST_CHANGES"


@pytest.mark.asyncio
async def test_github_review_task_plain_summary_defaults_to_comment_event(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(success=True, output="Summary only", data={})

    captured = {}

    async def _fake_execute_adapter_action(action_id, kwargs):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 3}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review.execute_skill", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action", _fake_execute_adapter_action)

    result = await run_github_review_task({"owner": "acme", "repo": "demo", "pull_number": 3})

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "COMMENT"
    assert result["review_event"] == "COMMENT"


@pytest.mark.asyncio
async def test_github_review_task_explicit_issue_comment_fallback(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(success=True, output="Fallback comment", data={})

    captured = {}

    async def _fake_execute_adapter_action(action_id, kwargs):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 4}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review.execute_skill", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action", _fake_execute_adapter_action)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 4, "writeback_mode": "issue_comment"}
    )

    assert result["success"] is True
    assert captured["action_id"] == "adapter:github:add_comment"
    assert "review_event" not in captured["kwargs"]


@pytest.mark.asyncio
async def test_github_review_task_head_sha_mismatch_suppresses_writeback(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(success=True, output="Looks stale now", data={"approved": True})

    called = {"adapter": False}

    async def _fake_execute_adapter_action(_action_id, _kwargs):
        called["adapter"] = True
        return {"success": True, "error": None, "result": {"id": 5}, "runtime_events": []}

    async def _fake_get_current_pr_head_sha(_owner, _repo, _pull_number):
        return "sha-new"

    monkeypatch.setattr("src.runtime.github_review.execute_skill", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action", _fake_execute_adapter_action)
    monkeypatch.setattr("src.runtime.github_review._get_current_pr_head_sha", _fake_get_current_pr_head_sha)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 12, "head_sha": "sha-old"}
    )

    assert called["adapter"] is False
    assert result["success"] is False
    assert result["error_code"] == "superseded_by_new_head_sha"
    assert result["stale"] is True
    assert result["review_written"] is False
    assert result["secondary_action_attempted"] is False
    assert result["expected_head_sha"] == "sha-old"
    assert result["current_head_sha"] == "sha-new"
    assert any(evt.get("event_type") == "task.github_review.superseded" for evt in result["runtime_events"])


@pytest.mark.asyncio
async def test_github_review_task_head_sha_match_still_writes_review(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(success=True, output="Still current", data={"approved": True})

    captured = {}

    async def _fake_execute_adapter_action(action_id, kwargs):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 6}, "runtime_events": []}

    async def _fake_get_current_pr_head_sha(_owner, _repo, _pull_number):
        return "sha-1"

    monkeypatch.setattr("src.runtime.github_review.execute_skill", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action", _fake_execute_adapter_action)
    monkeypatch.setattr("src.runtime.github_review._get_current_pr_head_sha", _fake_get_current_pr_head_sha)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 13, "head_sha": "sha-1"}
    )

    assert result["success"] is True
    assert captured["action_id"] == "adapter:github:review_pull_request"
    assert captured["kwargs"]["review_event"] == "APPROVE"
