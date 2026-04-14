import asyncio
import json

import pytest

from tests._import_helpers import load_module_from_repo_path

_event_bus_module = load_module_from_repo_path("src.gateway.event_bus", "src/gateway/event_bus.py")
EventBus = _event_bus_module.EventBus


@pytest.mark.asyncio
async def test_event_bus_unfiltered_listener_receives_all_events():
    bus = EventBus()
    queue = asyncio.Queue()
    await bus.add_listener(queue)

    await bus.emit("task.progress", {"session_id": "s1"})
    await bus.emit("task.progress", {"session_id": "s2"})

    first = json.loads(await queue.get())
    second = json.loads(await queue.get())
    assert first["type"] == "task.progress"
    assert first["data"]["session_id"] == "s1"
    assert "ts" in first
    assert second["data"]["session_id"] == "s2"


@pytest.mark.asyncio
async def test_event_bus_session_filter_only_receives_matching_session():
    bus = EventBus()
    queue = asyncio.Queue()
    await bus.add_listener(queue, filters={"session_id": " s-1 "})

    await bus.emit("task.progress", {"session_id": "s-2", "task_id": "t"})
    await bus.emit("task.progress", {"session_id": "s-1", "task_id": "t"})

    event = json.loads(await queue.get())
    assert event["data"]["session_id"] == "s-1"
    assert queue.empty()


@pytest.mark.asyncio
async def test_event_bus_task_filter_matches_alias_fields():
    bus = EventBus()
    queue_current = asyncio.Queue()
    queue_portal = asyncio.Queue()
    await bus.add_listener(queue_current, filters={"task_id": "task-1"})
    await bus.add_listener(queue_portal, filters={"task_id": "task-2"})

    await bus.emit("task.progress", {"current_task_id": "task-1"})
    bus.emit_sync("task.progress", {"portal_task_id": "task-2"})

    current_event = json.loads(await queue_current.get())
    portal_event = json.loads(await queue_portal.get())
    assert current_event["data"]["current_task_id"] == "task-1"
    assert portal_event["data"]["portal_task_id"] == "task-2"


@pytest.mark.asyncio
async def test_event_bus_group_and_coordination_filters():
    bus = EventBus()
    queue = asyncio.Queue()
    await bus.add_listener(
        queue,
        filters={"group_id": "group-1", "coordination_run_id": "coord-1"},
    )

    await bus.emit("coord.update", {"group_id": "group-1", "coordination_run_id": "coord-2"})
    await bus.emit("coord.update", {"portal_group_id": "group-1", "current_coordination_run_id": "coord-1"})

    event = json.loads(await queue.get())
    assert event["data"]["portal_group_id"] == "group-1"
    assert event["data"]["current_coordination_run_id"] == "coord-1"
    assert queue.empty()


@pytest.mark.asyncio
async def test_event_bus_request_id_filter_exact_match():
    bus = EventBus()
    queue = asyncio.Queue()
    await bus.add_listener(queue, filters={"request_id": "req-1"})

    await bus.emit("execution.progress", {"request_id": "req-2"})
    await bus.emit("execution.progress", {"request_id": "req-1"})

    event = json.loads(await queue.get())
    assert event["data"]["request_id"] == "req-1"
    assert queue.empty()


@pytest.mark.asyncio
async def test_event_bus_request_id_filter_matches_execution_id_alias():
    bus = EventBus()
    queue = asyncio.Queue()
    await bus.add_listener(queue, filters={"request_id": "req-exec-1"})

    await bus.emit("execution.progress", {"execution_id": "req-exec-1"})

    event = json.loads(await queue.get())
    assert event["data"]["execution_id"] == "req-exec-1"
    assert queue.empty()


@pytest.mark.asyncio
async def test_event_bus_request_id_and_session_id_combined_filter():
    bus = EventBus()
    queue = asyncio.Queue()
    await bus.add_listener(queue, filters={"session_id": "s-1", "request_id": "req-1"})

    await bus.emit("execution.progress", {"session_id": "s-1", "request_id": "req-x"})
    await bus.emit("execution.progress", {"session_id": "s-x", "request_id": "req-1"})
    await bus.emit("execution.progress", {"session_id": "s-1", "request_id": "req-1"})

    event = json.loads(await queue.get())
    assert event["data"]["session_id"] == "s-1"
    assert event["data"]["request_id"] == "req-1"
    assert queue.empty()


@pytest.mark.asyncio
async def test_event_bus_request_id_filter_does_not_match_parent_request_id():
    bus = EventBus()
    queue = asyncio.Queue()
    await bus.add_listener(queue, filters={"request_id": "req-1"})

    await bus.emit("execution.progress", {"parent_request_id": "req-1"})
    await bus.emit("execution.progress", {"request_id": "req-1"})

    event = json.loads(await queue.get())
    assert "parent_request_id" not in event["data"]
    assert event["data"]["request_id"] == "req-1"
    assert queue.empty()
