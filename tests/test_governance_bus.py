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
    async def _weird(_request):
        return {"status": "nonsense", "response": "x"}

    bus = build_default_execution_bus(chat_handler=_weird)
    req = make_execution_request(source_type="chat", execution_type="chat")

    result = await bus.execute(req)

    assert result.status == "error"
    assert result.audit_ref is not None
    assert any(event.get("event_type") == "governance.audit" for event in result.runtime_events)


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
