import pytest
from tests._optional_runtime_deps import skip_if_missing_ruamel_yaml

skip_if_missing_ruamel_yaml("full runtime dependencies unavailable (missing ruamel.yaml)")


class _SkillResult:
    def __init__(self, success=True, output="", error=None, data=None):
        self.success = success
        self.output = output
        self.error = error
        self.data = data or {}



def _chat_skill_result(output="", *, success=True, error=None, data=None, runtime_events=None):
    return {
        "success": success,
        "output": output,
        "error": error,
        "data": data or {},
        "runtime_events": runtime_events or [],
    }


@pytest.mark.asyncio
async def test_github_review_task_maps_approved_to_approve_event(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Looks good", data={"approved": True})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 1}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task({"owner": "acme", "repo": "demo", "pull_number": 1})

    assert result["success"] is True
    assert captured["action_id"] == "adapter:github:review_pull_request"
    assert captured["kwargs"]["review_event"] == "APPROVE"
    assert result["review_event"] == "APPROVE"


@pytest.mark.asyncio
async def test_github_review_task_maps_rejected_to_request_changes_event(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Need changes", data={"approved": False})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 2}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task({"owner": "acme", "repo": "demo", "pull_number": 2})

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "REQUEST_CHANGES"
    assert result["review_event"] == "REQUEST_CHANGES"


@pytest.mark.asyncio
async def test_github_review_task_plain_summary_defaults_to_comment_event(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Summary only", data={})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 3}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task({"owner": "acme", "repo": "demo", "pull_number": 3})

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "COMMENT"
    assert result["review_event"] == "COMMENT"


@pytest.mark.asyncio
async def test_github_review_task_uses_payload_review_event_when_skill_has_no_event(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Summary only", data={})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 31}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 3, "review_event": "COMMENT"}
    )

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "COMMENT"
    assert result["review_event"] == "COMMENT"


@pytest.mark.asyncio
async def test_github_review_task_payload_approve_used_as_fallback_default(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Looks fine", data={"review_summary": "Looks fine"})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 35}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 3, "review_event": "APPROVE"}
    )

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "APPROVE"
    assert result["review_event"] == "APPROVE"


@pytest.mark.asyncio
async def test_github_review_task_explicit_skill_review_event_wins_over_payload_default(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Needs work", data={"review_summary": "Needs work", "review_event": "REQUEST_CHANGES"})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 36}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 3, "review_event": "APPROVE"}
    )

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "REQUEST_CHANGES"
    assert result["review_event"] == "REQUEST_CHANGES"


@pytest.mark.asyncio
async def test_github_review_task_requested_review_event_field_does_not_override_decision_logic(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Looks fine", data={"review_summary": "Looks fine", "requested_review_event": "APPROVE"})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 37}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 3, "review_event": "APPROVE"}
    )

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "APPROVE"
    assert result["review_event"] == "APPROVE"


@pytest.mark.asyncio
async def test_github_review_task_invalid_payload_review_event_falls_back_to_comment(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Summary only", data={})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 32}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 3, "review_event": "bad"}
    )

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "COMMENT"
    assert result["review_event"] == "COMMENT"


@pytest.mark.asyncio
async def test_github_review_task_skill_event_overrides_payload_default(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Needs changes", data={"review_event": "REQUEST_CHANGES"})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 33}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 3, "review_event": "COMMENT"}
    )

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "REQUEST_CHANGES"
    assert result["review_event"] == "REQUEST_CHANGES"


@pytest.mark.asyncio
async def test_github_review_task_skill_approved_inference_overrides_payload_default(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Looks good", data={"approved": True})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 34}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 3, "review_event": "COMMENT"}
    )

    assert result["success"] is True
    assert captured["kwargs"]["review_event"] == "APPROVE"
    assert result["review_event"] == "APPROVE"


@pytest.mark.asyncio
async def test_github_review_task_explicit_issue_comment_fallback(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Fallback comment", data={})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 4}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 4, "writeback_mode": "issue_comment"}
    )

    assert result["success"] is True
    assert captured["action_id"] == "adapter:github:add_comment"
    assert "review_event" not in captured["kwargs"]


@pytest.mark.asyncio
async def test_github_review_task_head_sha_mismatch_suppresses_writeback(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    called = {"skill": False, "adapter": False}

    async def _fake_execute_skill(*_args, **_kwargs):
        called["skill"] = True
        return _SkillResult(success=True, output="Looks stale now", data={"approved": True})

    async def _fake_execute_adapter_action_via_bus(_action_id, _kwargs, **_meta):
        called["adapter"] = True
        return {"success": True, "error": None, "result": {"id": 5}, "runtime_events": []}

    async def _unexpected_execute_github_review_action(*_args, **_kwargs):
        raise AssertionError("writeback helper should not run when head_sha is stale")

    async def _fake_get_current_pr_head_sha(_owner, _repo, _pull_number):
        return "sha-new"

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)
    monkeypatch.setattr("src.runtime.github_review._get_current_pr_head_sha", _fake_get_current_pr_head_sha)
    monkeypatch.setattr("src.runtime.github_review.execute_github_review_action", _unexpected_execute_github_review_action)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": 12,
            "head_sha": "sha-old",
            "source": "automation_rule",
            "rule_id": "rule-12",
            "automation_rule_id": "ar-12",
            "dedupe_key": "dedupe-12",
            "review_target": {"type": "team", "name": "acme/reviewers"},
        }
    )

    assert called["skill"] is False
    assert called["adapter"] is False
    assert result["success"] is False
    assert result["error_code"] == "superseded_by_new_head_sha"
    assert result["stale"] is True
    assert result["review_written"] is False
    assert result["secondary_action_attempted"] is False
    assert result["expected_head_sha"] == "sha-old"
    assert result["current_head_sha"] == "sha-new"
    assert result["automation_rule_id"] == "ar-12"
    assert result["dedupe_key"] == "dedupe-12"
    assert any(evt.get("event_type") == "task.github_review.superseded" for evt in result["runtime_events"])


@pytest.mark.asyncio
async def test_github_review_task_precheck_freshness_failure_does_not_block_skill(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    called = {"skill": False, "adapter": False}

    async def _fake_execute_skill(*_args, **_kwargs):
        called["skill"] = True
        return _SkillResult(success=True, output="Review content", data={})

    async def _fake_execute_adapter_action_via_bus(_action_id, _kwargs, **_meta):
        called["adapter"] = True
        return {"success": True, "error": None, "result": {"id": 51}, "runtime_events": []}

    async def _fake_execute_github_review_action(action_id, kwargs, payload):
        called["adapter"] = True
        return {"success": True, "error": None, "result": {"id": 51}, "runtime_events": []}

    async def _flaky_get_current_pr_head_sha(_owner, _repo, _pull_number):
        raise RuntimeError("temporary api failure")

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)
    monkeypatch.setattr("src.runtime.github_review.execute_github_review_action", _fake_execute_github_review_action)
    monkeypatch.setattr("src.runtime.github_review._get_current_pr_head_sha", _flaky_get_current_pr_head_sha)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 12, "head_sha": "sha-old"}
    )

    assert called["skill"] is True
    assert called["adapter"] is True
    assert result["success"] is True
    assert any(evt.get("event_type") == "task.github_review.freshness_guard.warning" for evt in result["runtime_events"])


@pytest.mark.asyncio
async def test_github_review_task_head_sha_match_still_writes_review(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(success=True, output="Still current", data={"approved": True})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 6}, "runtime_events": []}

    async def _fake_get_current_pr_head_sha(_owner, _repo, _pull_number):
        return "sha-1"

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)
    monkeypatch.setattr("src.runtime.github_review._get_current_pr_head_sha", _fake_get_current_pr_head_sha)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 13, "head_sha": "sha-1"}
    )

    assert result["success"] is True
    assert captured["action_id"] == "adapter:github:review_pull_request"
    assert captured["kwargs"]["review_event"] == "APPROVE"


@pytest.mark.asyncio
async def test_github_review_task_gate_with_blocked_false_allows_secondary_action(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Looks good", data={"approved": True})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 7}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": 21,
            "_action_gate": lambda *_args, **_kwargs: {"blocked": False},
        }
    )

    assert result["success"] is True
    assert captured["action_id"] == "adapter:github:review_pull_request"
    assert result["secondary_action_success"] is True


@pytest.mark.asyncio
async def test_github_review_task_gate_with_blocked_true_blocks_secondary_action(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(success=True, output="Needs changes", data={"approved": False})

    called = {"adapter": False}

    async def _fake_execute_adapter_action_via_bus(_action_id, _kwargs, **_meta):
        called["adapter"] = True
        return {"success": True, "error": None, "result": {"id": 8}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": 22,
            "_action_gate": lambda *_args, **_kwargs: {
                "blocked": True,
                "reason": "denied_by_policy",
                "message": "blocked by test",
            },
        }
    )

    assert called["adapter"] is False
    assert result["success"] is False
    assert result["secondary_action_attempted"] is True
    assert result["secondary_action_success"] is False
    assert "capability policy blocked for secondary action" in str(result["error"])
    assert any(evt.get("event_type") == "task.github_review.secondary_action.blocked" for evt in result["runtime_events"])
    assert len(result["actions_applied"]) == 1
    assert result["actions_applied"][0]["action_id"] == "adapter:github:review_pull_request"
    assert result["actions_applied"][0]["blocked"] is True
    assert result["actions_applied"][0]["reason"] == "denied_by_policy"
    assert result["actions_applied"][0]["message"] == "blocked by test"


@pytest.mark.asyncio
async def test_github_review_task_forwards_runtime_context_to_bus_helper(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _chat_skill_result("Looks good", data={"approved": True})

    captured = {}

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **meta):
        captured["action_id"] = action_id
        captured["kwargs"] = kwargs
        captured["meta"] = meta
        return {"success": True, "error": None, "result": {"id": 9}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": 23,
            "session_id": "s-123",
            "agent_id": "agent-123",
            "policy_profile_id": "pp-123",
            "_execution_metadata": {"k": "v"},
        }
    )

    assert result["success"] is True
    assert captured["action_id"] == "adapter:github:review_pull_request"
    assert captured["meta"]["source_type"] == "runtime"
    assert captured["meta"]["source_ref"] == "github_review"
    assert captured["meta"]["session_id"] == "s-123"
    assert captured["meta"]["agent_id"] == "agent-123"
    assert captured["meta"]["policy_profile_id"] == "pp-123"
    assert captured["meta"]["metadata"] == {"k": "v"}


@pytest.mark.asyncio
async def test_github_review_task_failed_skill_without_error_uses_output_as_error(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(
            success=False,
            error=None,
            output="Review skill failed because repository context was unavailable",
            data={},
        )

    async def _fail_bus_call(*_args, **_kwargs):
        raise AssertionError("secondary write-back should not run for failed skill")

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fail_bus_call)

    result = await run_github_review_task({"owner": "acme", "repo": "demo", "pull_number": 24})

    assert result["success"] is False
    assert result["error"] == "Review skill failed because repository context was unavailable"
    failed_events = [evt for evt in result["runtime_events"] if evt.get("event_type") == "task.github_review.failed"]
    assert failed_events
    assert failed_events[-1]["detail_payload"]["error"]


@pytest.mark.asyncio
async def test_github_review_task_failed_skill_without_error_or_output_uses_generic_error(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    async def _fake_execute_skill(*_args, **_kwargs):
        return _SkillResult(success=False, error=None, output="", data={})

    async def _fail_bus_call(*_args, **_kwargs):
        raise AssertionError("secondary write-back should not run for failed skill")

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fail_bus_call)

    result = await run_github_review_task({"owner": "acme", "repo": "demo", "pull_number": 25})

    assert result["success"] is False
    assert "GitHub review skill 'review-pull-request' failed without an explicit error" in result["error"]
    assert result["secondary_action_attempted"] is False


@pytest.mark.asyncio
async def test_github_review_task_parses_json_object_string_skill_kwargs(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    captured = {}

    async def _fake_execute_skill(**kwargs):
        captured["skill_name"] = kwargs["skill_name"]
        captured["kwargs"] = kwargs
        return _SkillResult(success=False, output="stop", error="stop", data={})

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": 30,
            "skill_kwargs": '{"mode":"strict","max_comments":2}',
        }
    )

    assert captured["skill_name"] == "review-pull-request"
    assert captured["kwargs"]["skill_kwargs"]["mode"] == "strict"
    assert captured["kwargs"]["skill_kwargs"]["max_comments"] == 2
    assert result["success"] is False


@pytest.mark.asyncio
async def test_github_review_task_invalid_json_skill_kwargs_returns_structured_failure(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    called = {"skill": False}

    async def _fake_execute_skill(*_args, **_kwargs):
        called["skill"] = True
        return _SkillResult(success=True, output="ok", error=None, data={})

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 31, "skill_kwargs": "{not-json"}
    )

    assert called["skill"] is False
    assert result["success"] is False
    assert result["error_code"] == "invalid_skill_kwargs_json"
    assert result["secondary_action_attempted"] is False


@pytest.mark.asyncio
async def test_github_review_task_non_object_json_skill_kwargs_returns_structured_failure(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    called = {"skill": False}

    async def _fake_execute_skill(*_args, **_kwargs):
        called["skill"] = True
        return _SkillResult(success=True, output="ok", error=None, data={})

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 32, "skill_kwargs": "[1,2]"}
    )

    assert called["skill"] is False
    assert result["success"] is False
    assert result["error_code"] == "invalid_skill_kwargs_type"


@pytest.mark.asyncio
async def test_github_review_task_non_dict_skill_kwargs_returns_structured_failure(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    called = {"skill": False}

    async def _fake_execute_skill(*_args, **_kwargs):
        called["skill"] = True
        return _SkillResult(success=True, output="ok", error=None, data={})

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)

    result = await run_github_review_task(
        {"owner": "acme", "repo": "demo", "pull_number": 33, "skill_kwargs": [1, 2]}
    )

    assert called["skill"] is False
    assert result["success"] is False
    assert result["error_code"] == "invalid_skill_kwargs_type"


@pytest.mark.asyncio
async def test_github_review_task_accepts_portal_automation_payload_shape(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    captured = {}

    async def _fake_execute_skill(**kwargs):
        captured["skill_name"] = kwargs["skill_name"]
        captured["kwargs"] = kwargs
        return _SkillResult(success=True, output="Automated review", data={"review_event": "COMMENT"})

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        captured["action_id"] = action_id
        captured["action_kwargs"] = kwargs
        return {"success": True, "error": None, "result": {"id": 88}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)
    async def _same_head_sha(_owner, _repo, _pull_number):
        return "abc123"

    monkeypatch.setattr("src.runtime.github_review._get_current_pr_head_sha", _same_head_sha)

    result = await run_github_review_task(
        {
            "source": "automation_rule",
            "rule_id": "rule-1",
            "automation_rule_id": "auto-1",
            "provider": "github",
            "owner": "acme",
            "repo": "engineering-flow-platform",
            "pull_number": "123",
            "head_sha": "abc123",
            "review_target": {"type": "team", "name": "acme/platform-reviewers"},
            "task_type": "github_review_task",
            "skill_name": "review-pull-request",
            "review_event": "COMMENT",
            "dedupe_key": "dedupe-1",
        }
    )

    assert result["success"] is True
    assert captured["skill_name"] == "review-pull-request"
    assert captured["kwargs"]["pull_number"] == 123
    assert captured["kwargs"]["requested_head_sha"] == "abc123"
    assert captured["kwargs"]["review_target"] == {"type": "team", "name": "acme/platform-reviewers"}
    assert captured["kwargs"]["requested_event"] == "COMMENT"
    assert captured["action_id"] == "adapter:github:review_pull_request"
    assert captured["action_kwargs"]["pull_number"] == 123
    assert result["rule_id"] == "rule-1"
    assert result["automation_rule_id"] == "auto-1"
    assert result["dedupe_key"] == "dedupe-1"
    assert result["review_target"] == {"type": "team", "name": "acme/platform-reviewers"}
    assert any(
        (evt.get("detail_payload") or {}).get("automation_rule_id") == "auto-1"
        for evt in result["runtime_events"]
    )


@pytest.mark.asyncio
async def test_github_review_task_explicit_skill_kwargs_override_payload_defaults(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    captured = {}

    async def _fake_execute_skill(**kwargs):
        captured["kwargs"] = kwargs
        return _SkillResult(success=True, output="Automated review", data={"review_event": "COMMENT"})

    async def _fake_execute_adapter_action_via_bus(action_id, kwargs, **_meta):
        return {"success": True, "error": None, "result": {"id": 89}, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)
    monkeypatch.setattr("src.runtime.github_review.execute_adapter_action_via_bus", _fake_execute_adapter_action_via_bus)
    async def _same_head_sha(_owner, _repo, _pull_number):
        return "abc123"

    monkeypatch.setattr("src.runtime.github_review._get_current_pr_head_sha", _same_head_sha)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "engineering-flow-platform",
            "pull_number": "123",
            "head_sha": "abc123",
            "review_target": {"type": "team", "name": "acme/platform-reviewers"},
            "skill_kwargs": {"head_sha": "override-sha", "review_target": {"type": "user", "name": "alice"}},
        }
    )

    assert result["success"] is True
    assert captured["kwargs"]["skill_kwargs"]["head_sha"] == "override-sha"
    assert captured["kwargs"]["skill_kwargs"]["review_target"] == {"type": "user", "name": "alice"}


@pytest.mark.asyncio
async def test_github_review_task_invalid_pull_number_returns_clear_error(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    called = {"skill": False}

    async def _fake_execute_skill(*_args, **_kwargs):
        called["skill"] = True
        return _SkillResult(success=True, output="unused", data={})

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_execute_skill)

    result = await run_github_review_task(
        {
            "owner": "acme",
            "repo": "demo",
            "pull_number": "abc",
            "source": "automation_rule",
            "rule_id": "rule-err",
            "automation_rule_id": "auto-err",
            "dedupe_key": "dedupe-err",
        }
    )

    assert called["skill"] is False
    assert result["success"] is False
    assert result["error_code"] == "invalid_pull_number"
    assert result["rule_id"] == "rule-err"
    assert result["automation_rule_id"] == "auto-err"
    assert result["dedupe_key"] == "dedupe-err"


@pytest.mark.asyncio
async def test_github_review_task_missing_required_fields_preserves_trace_fields():
    from src.runtime.github_review import run_github_review_task

    result = await run_github_review_task(
        {
            "source": "automation_rule",
            "rule_id": "rule-missing",
            "automation_rule_id": "auto-missing",
            "dedupe_key": "dedupe-missing",
            "pull_number": 1,
        }
    )

    assert result["success"] is False
    assert result["error"] == "owner, repo, and pull_number are required"
    assert result["rule_id"] == "rule-missing"
    assert result["automation_rule_id"] == "auto-missing"
    assert result["dedupe_key"] == "dedupe-missing"


@pytest.mark.asyncio
async def test_github_review_task_uses_chat_tool_loop_not_execute_skill(monkeypatch):
    from src.runtime.github_review import run_github_review_task

    called = {"chat": False}

    async def _fake_chat_loop(**_kwargs):
        called["chat"] = True
        return _chat_skill_result("## Pull Request Summary\nLooks good.", data={"review_summary": "## Pull Request Summary\nLooks good.", "requested_review_event": "COMMENT", "execution_mode": "chat_tool_loop"})

    async def fake_success_action(*_args, **_kwargs):
        return {"success": True, "error": None, "runtime_events": []}

    monkeypatch.setattr("src.runtime.github_review._execute_review_skill_via_chat_loop", _fake_chat_loop)
    monkeypatch.setattr("src.runtime.github_review.execute_github_review_action", fake_success_action)

    result = await run_github_review_task({"owner": "acme", "repo": "demo", "pull_number": 1})

    assert called["chat"] is True
    assert result["success"] is True
    assert result["result"]["skill"]["data"]["execution_mode"] == "chat_tool_loop"


def test_build_github_review_chat_prompt_contains_handoff_constraints():
    from src.runtime.github_review import _build_github_review_chat_prompt

    prompt = _build_github_review_chat_prompt(
        skill_name="review-pull-request", owner="acme", repo="demo", pull_number=7,
        requested_head_sha="abc123", review_target={"type": "pull_request"}, requested_event="COMMENT",
        writeback_mode="review_pull_request", runtime_managed_writeback=True,
    )

    assert prompt.startswith("/skill use review-pull-request")
    assert "Repository: acme/demo" in prompt
    assert "Pull request: #7" in prompt
    assert "Expected head SHA: abc123" in prompt
    assert "Do NOT call github_add_comment" in prompt
    assert "Do NOT call github_add_pr_review_comment" in prompt
    assert "runtime task wrapper" in prompt
