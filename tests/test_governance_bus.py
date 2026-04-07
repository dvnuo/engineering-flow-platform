import pytest

from src.runtime.contracts import make_execution_request, make_execution_result
from src.runtime.execution_bus import ExecutionBus, build_default_execution_bus
from src.runtime.governance import GovernanceHooks
from src.runtime.governance_bus import (
    GovernanceAuditRecord,
    GovernanceBus,
    GovernanceDecision,
    build_default_governance_bus,
)


@pytest.mark.asyncio
async def test_governance_before_execute_can_block_request():
    class _BlockingGovernance(GovernanceBus):
        async def before_execute(self, request):
            return GovernanceDecision(
                allowed=False,
                reason="manual_block",
                audit_record=GovernanceAuditRecord(
                    audit_ref="audit-1",
                    stage="before_execute",
                    message="blocked",
                    metadata={"rule": "manual"},
                ),
            )

    async def _handler(_request):
        raise AssertionError("handler should not run")

    bus = ExecutionBus(governance=_BlockingGovernance())
    bus.register_handler("chat", _handler)
    req = make_execution_request(source_type="chat", execution_type="chat")

    result = await bus.execute(req)

    assert result.status == "blocked"
    assert result.output_payload["reason"] == "manual_block"
    assert result.audit_ref == "audit-1"
    assert result.runtime_events and result.runtime_events[0]["event_type"] == "governance.audit"


@pytest.mark.asyncio
async def test_governance_after_execute_can_enrich_result():
    class _EnrichingGovernance(GovernanceBus):
        async def after_execute(self, request, result):
            result.artifacts["governance"] = {"flag": True}
            result.runtime_events.append({"event_type": "governance.enriched"})
            result.audit_ref = "audit-enriched"
            result.next_action_hint = "continue"
            return result

    async def _ok(_request):
        return {"response": "ok"}

    bus = ExecutionBus(governance=_EnrichingGovernance())
    bus.register_handler("chat", _ok)
    req = make_execution_request(source_type="chat", execution_type="chat")

    result = await bus.execute(req)

    assert result.status == "success"
    assert result.artifacts["governance"]["flag"] is True
    assert result.runtime_events[-1]["event_type"] == "governance.enriched"
    assert result.audit_ref == "audit-enriched"
    assert result.next_action_hint == "continue"


@pytest.mark.asyncio
async def test_governance_on_error_attaches_audit_without_changing_error_payload():
    class _ErrorAuditGovernance(GovernanceBus):
        async def on_error(self, request, error):
            return GovernanceAuditRecord(
                audit_ref="audit-error",
                stage="on_error",
                message="captured",
                metadata={"error_type": error.__class__.__name__},
            )

    async def _boom(_request):
        raise RuntimeError("boom")

    bus = ExecutionBus(governance=_ErrorAuditGovernance())
    bus.register_handler("chat", _boom)
    req = make_execution_request(source_type="chat", execution_type="chat")

    result = await bus.execute(req)

    assert result.status == "error"
    assert result.output_payload["error"] == "boom"
    assert result.output_payload["error_type"] == "RuntimeError"
    assert result.audit_ref == "audit-error"


@pytest.mark.asyncio
async def test_legacy_governance_hooks_still_supported():
    class _LegacyHooks(GovernanceHooks):
        def __init__(self):
            self.calls = []

        def before_execute(self, request):
            self.calls.append("before")

        def after_execute(self, request, result):
            self.calls.append("after")

    async def _ok(_request):
        return {"response": "ok"}

    bus = ExecutionBus(governance=_LegacyHooks())
    bus.register_handler("chat", _ok)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)

    assert result.status == "success"
    assert bus._governance._hooks.calls == ["before", "after"]


@pytest.mark.asyncio
async def test_governance_exceptions_are_swallowed_and_logged(caplog):
    class _FailingGovernance(GovernanceBus):
        async def before_execute(self, request):
            raise RuntimeError("before failed")

        async def after_execute(self, request, result):
            raise RuntimeError("after failed")

        async def on_error(self, request, error):
            raise RuntimeError("on_error failed")

    async def _boom(_request):
        raise RuntimeError("handler boom")

    with caplog.at_level("DEBUG"):
        bus = ExecutionBus(governance=_FailingGovernance())
        bus.register_handler("chat", _boom)
        req = make_execution_request(source_type="chat", execution_type="chat")
        result = await bus.execute(req)

    assert result.status == "error"
    assert "ExecutionBus governance hook failed: before_execute" in caplog.text
    assert "ExecutionBus governance hook failed: on_error" in caplog.text
    assert "ExecutionBus governance hook failed: after_execute" in caplog.text


@pytest.mark.asyncio
async def test_default_governance_invalid_status_is_coerced_safely():
    async def _weird_task(_request):
        return {"status": "nonsense", "response": "x"}

    bus = ExecutionBus(governance=build_default_governance_bus())
    bus.register_handler("task", _weird_task)
    req = make_execution_request(source_type="agent", execution_type="task", input_payload={"task_type": "demo"})

    result = await bus.execute(req)

    assert result.status == "error"
    assert result.audit_ref is not None
    assert any(event.get("event_type") == "governance.audit" for event in result.runtime_events)


@pytest.mark.asyncio
async def test_default_governance_task_output_payload_non_dict_is_wrapped():
    async def _task_handler(_request):
        from src.runtime.contracts import ExecutionResult

        return ExecutionResult(
            request_id="task-raw-1",
            status="success",
            output_payload="string-result",  # type: ignore[arg-type]
        )

    bus = ExecutionBus(governance=build_default_governance_bus())
    bus.register_handler("task", _task_handler)
    req = make_execution_request(source_type="agent", execution_type="task", input_payload={"task_type": "demo"})

    result = await bus.execute(req)

    assert result.status == "success"
    assert isinstance(result.output_payload, dict)
    assert "value" in result.output_payload


@pytest.mark.asyncio
async def test_default_governance_event_result_status_normalization():
    async def _event_handler(_request):
        return make_execution_result(
            request_id="evt-1",
            status="bad-status",
            output_payload={"message": "x"},
        )

    bus = ExecutionBus(governance=build_default_governance_bus())
    bus.register_handler("event", _event_handler)
    req = make_execution_request(source_type="system", execution_type="event", input_payload={"target_execution_type": "chat"})

    result = await bus.execute(req)

    assert result.status == "error"
    assert any(event.get("event_type") == "governance.audit" for event in result.runtime_events)


@pytest.mark.asyncio
async def test_legacy_governance_after_execute_return_is_coerced_to_execution_result():
    class _LegacyHooks(GovernanceHooks):
        def after_execute(self, request, result):
            from src.runtime.contracts import ExecutionResult

            return ExecutionResult(
                request_id="legacy-inner",
                status="",
                output_payload="raw-string",  # type: ignore[arg-type]
            )

    async def _ok(_request):
        return {"response": "ok"}

    bus = ExecutionBus(governance=_LegacyHooks())
    bus.register_handler("chat", _ok)
    req = make_execution_request(source_type="chat", execution_type="chat")
    result = await bus.execute(req)

    assert isinstance(result.output_payload, dict)
    assert result.request_id == req.request_id


@pytest.mark.asyncio
async def test_default_governance_after_execute_order_normalize_then_policy_then_audit():
    bus = build_default_governance_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "tool_task", "tool_name": "demo_tool"},
        metadata={"latest_user_message": "show me result", "tool_calls_count": 1},
    )
    # deliberately invalid status + non-dict output to trigger normalize path and notes
    from src.runtime.contracts import ExecutionResult
    result = ExecutionResult(
        request_id=req.request_id,
        status="invalid",
        output_payload="payload",  # type: ignore[arg-type]
    )

    final_result = await bus.after_execute(req, result)

    assert final_result.status == "error"
    assert isinstance(final_result.output_payload, dict)
    assert final_result.runtime_events
    last_event = final_result.runtime_events[-1]
    assert last_event.get("event_type") == "governance.audit"
    notes = last_event.get("detail_payload", {}).get("notes", [])
    assert "invalid_status_coerced" in notes
    assert "output_payload_normalized" in notes


@pytest.mark.asyncio
async def test_default_governance_policy_can_set_passthrough_recommendation():
    bus = build_default_governance_bus()
    req = make_execution_request(
        source_type="agent",
        execution_type="task",
        input_payload={"task_type": "tool_task", "tool_name": "jira_get_issue"},
        metadata={"latest_user_message": "show issue details", "tool_calls_count": 1, "tool_name": "jira_get_issue"},
    )
    result = make_execution_result(
        request_id=req.request_id,
        status="success",
        output_payload={"content": "Issue details"},
        artifacts={},
    )

    final_result = await bus.after_execute(req, result)

    governance_artifacts = final_result.artifacts.get("governance", {})
    assert governance_artifacts.get("tool_result_passthrough_recommended") is True


@pytest.mark.asyncio
async def test_build_default_execution_bus_uses_default_governance_bus():
    bus = build_default_execution_bus()
    assert bus._governance.__class__.__name__ == build_default_governance_bus().__class__.__name__


@pytest.mark.asyncio
async def test_default_governance_auto_run_guard_blocks():
    async def _ok(_request):
        return make_execution_result(request_id="x", status="success")

    bus = build_default_execution_bus(chat_handler=_ok)
    req = make_execution_request(
        source_type="chat",
        execution_type="chat",
        metadata={
            "auto_run": True,
            "governance_require_explicit_allow": True,
            "governance_allow_auto_run": False,
        },
    )

    result = await bus.execute(req)

    assert result.status == "blocked"
    assert result.output_payload["reason"] == "auto_run_guard_blocked"


@pytest.mark.asyncio
async def test_default_governance_external_trigger_allowlist_blocks_non_member():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="system",
        execution_type="event",
        input_payload={"target_execution_type": "tool"},
        metadata={
            "external_triggered": True,
            "governance_external_allowlist": ["chat"],
        },
    )

    result = await bus.execute(req)

    assert result.status == "blocked"
    assert result.output_payload["reason"] == "external_allowlist"


@pytest.mark.asyncio
async def test_default_governance_denied_capability_ids_blocks_adapter_action():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "adapter_action_task", "action_id": "adapter:jira:read_issue", "kwargs": {"issue_key": "ENG-1"}},
        metadata={"denied_capability_ids": ["adapter:jira:read_issue"]},
    )
    result = await bus.execute(req)
    assert result.status == "blocked"
    assert result.output_payload["reason"] == "denied_capability_ids"


@pytest.mark.asyncio
async def test_default_governance_allowed_capability_types_missing_match_blocks():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "adapter_action_task", "action_id": "adapter:jira:read_issue", "kwargs": {"issue_key": "ENG-1"}},
        metadata={"allowed_capability_types": ["tool"]},
    )
    result = await bus.execute(req)
    assert result.status == "blocked"
    assert result.output_payload["reason"] == "allowed_capability_types"


@pytest.mark.asyncio
async def test_default_governance_malformed_capability_lists_do_not_crash():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "tool_task", "tool_name": "read", "kwargs": {"file_path": "README.md"}},
        metadata={
            "allowed_capability_ids": "not-a-list",
            "denied_capability_ids": {"x": 1},
            "allowed_capability_types": None,
            "denied_adapter_actions": 7,
        },
    )
    result = await bus.execute(req)
    assert result.status in {"success", "error", "blocked"}


@pytest.mark.asyncio
async def test_default_governance_allows_bare_tool_capability_name():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "tool_task", "tool_name": "shell", "kwargs": {"cmd": "echo hi"}},
        metadata={"allowed_capability_ids": ["shell"]},
    )
    result = await bus.execute(req)
    assert result.status in {"success", "error"}


@pytest.mark.asyncio
async def test_default_governance_allows_capability_type_alias_action():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "adapter_action_task", "action_id": "adapter:jira:read_issue", "kwargs": {"issue_key": "ENG-1"}},
        metadata={"allowed_capability_types": ["action"]},
    )
    result = await bus.execute(req)
    assert result.status in {"success", "error"}


@pytest.mark.asyncio
async def test_default_governance_allows_capability_type_alias_channel():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "adapter_action_task", "action_id": "adapter:jira:read_issue", "kwargs": {"issue_key": "ENG-1"}},
        metadata={"allowed_capability_types": ["channel"]},
    )
    result = await bus.execute(req)
    assert result.status == "blocked"
    assert result.output_payload["reason"] == "allowed_capability_types"


@pytest.mark.asyncio
async def test_default_governance_allowed_actions_alias_supports_action_name():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "adapter_action_task", "action_id": "adapter:github:add_comment", "kwargs": {"owner": "acme", "repo": "demo", "pull_number": 1, "comment": "ok"}},
        metadata={"allowed_actions": ["add_comment"]},
    )
    result = await bus.execute(req)
    assert result.status in {"success", "error"}


@pytest.mark.asyncio
async def test_default_governance_denied_actions_alias_blocks_by_action_name():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "adapter_action_task", "action_id": "adapter:github:add_comment", "kwargs": {"owner": "acme", "repo": "demo", "pull_number": 1, "comment": "ok"}},
        metadata={"denied_actions": ["add_comment"]},
    )
    result = await bus.execute(req)
    assert result.status == "blocked"
    assert result.output_payload["reason"] == "denied_adapter_actions"


@pytest.mark.asyncio
async def test_default_governance_denied_actions_blocks_jira_transition_action_name():
    bus = build_default_execution_bus()
    req = make_execution_request(
        source_type="task",
        execution_type="task",
        input_payload={"task_type": "adapter_action_task", "action_id": "adapter:jira:transition_issue", "kwargs": {"issue_key": "ENG-1", "transition": "Done"}},
        metadata={"denied_actions": ["transition_issue"]},
    )
    result = await bus.execute(req)
    assert result.status == "blocked"
    assert result.output_payload["reason"] == "denied_adapter_actions"
