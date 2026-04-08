import pytest


@pytest.mark.asyncio
async def test_execute_adapter_action_via_bus_uses_unique_request_ids(monkeypatch):
    from src.runtime import runtime_adapter_execution as module

    captured_request_ids = []

    async def _fake_execute_runtime_task_request(**kwargs):
        captured_request_ids.append(kwargs["request_id"])
        return type(
            "R",
            (),
            {
                "status": "success",
                "output_payload": {"success": True, "result": {"ok": True}},
                "runtime_events": [],
            },
        )()

    monkeypatch.setattr(module, "execute_runtime_task_request", _fake_execute_runtime_task_request)

    await module.execute_adapter_action_via_bus("adapter:jira:read_issue", {"issue_key": "ABC-1"})
    await module.execute_adapter_action_via_bus("adapter:jira:read_issue", {"issue_key": "ABC-1"})

    assert len(captured_request_ids) == 2
    assert captured_request_ids[0] != captured_request_ids[1]
    assert captured_request_ids[0].startswith("runtime-adapter:jira:read_issue-")
    assert captured_request_ids[1].startswith("runtime-adapter:jira:read_issue-")
