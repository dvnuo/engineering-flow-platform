from __future__ import annotations

from pathlib import Path
from typing import Any

from efp_runtime.opencode_parity import (
    CAPABILITY_GROUPS,
    DEFAULT_CORE_TOOL_IDS,
    EXCLUDED_TOOL_IDS,
    LEGACY_ALIAS_TOOL_IDS,
    OPTIONAL_CONDITIONAL_TOOL_IDS,
)
from efp_runtime.skills.discovery import SkillDiscovery
from efp_runtime.tools.builtin import WebSearchRequest, create_core_tool_registry
from efp_runtime.tools.builtin.task import TaskToolRequest


ALLOWED_STATUSES = {"done", "conditional", "excluded", "remaining"}


async def _task_runner(request: TaskToolRequest) -> str:
    return f"completed {request.description}"


def _websearch_runner(request: WebSearchRequest) -> str:
    return f"results for {request.query}"


def test_default_registry_matches_manifest_default_core_ids(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert tuple(registry.ids()) == DEFAULT_CORE_TOOL_IDS


def test_conditional_manifest_tool_ids_are_registrable(tmp_path: Path):
    registry = create_core_tool_registry(
        tmp_path,
        include_legacy_aliases=True,
        task_runner=_task_runner,
        allow_background_task=True,
        include_question_tool=True,
        skill_discovery=SkillDiscovery([]),
        include_skill_list_tool=True,
        include_lsp_tool=True,
        include_plan_tool=True,
        websearch_runner=_websearch_runner,
    )

    assert "websearch" in OPTIONAL_CONDITIONAL_TOOL_IDS
    assert set(OPTIONAL_CONDITIONAL_TOOL_IDS).issubset(registry.ids())


def test_legacy_aliases_are_not_default_core_ids(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert set(LEGACY_ALIAS_TOOL_IDS).isdisjoint(DEFAULT_CORE_TOOL_IDS)
    assert set(LEGACY_ALIAS_TOOL_IDS).isdisjoint(registry.ids())


def test_capability_group_statuses_are_explicit_and_known():
    expected_groups = {
        "loop",
        "permissions",
        "tool lifecycle",
        "skills",
        "commands",
        "context/compaction",
        "Copilot provider",
        "session state",
    }

    assert expected_groups.issubset(CAPABILITY_GROUPS)
    for name, entry in CAPABILITY_GROUPS.items():
        assert name.strip()
        assert entry.status in ALLOWED_STATUSES
        assert entry.summary.strip()


def test_remaining_manifest_items_have_concrete_next_actions():
    vague_actions = {"", "todo", "tbd", "follow up", "investigate", "later"}

    for name, entry in _manifest_entries():
        if entry.status != "remaining":
            continue
        action = (entry.next_action or "").strip()
        assert action.lower() not in vague_actions, name
        assert len(action.split()) >= 8, name


def test_manifest_has_no_remaining_items():
    assert [
        name for name, entry in _manifest_entries() if entry.status == "remaining"
    ] == []


def _manifest_entries() -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    entries.extend(CAPABILITY_GROUPS.items())
    entries.extend(OPTIONAL_CONDITIONAL_TOOL_IDS.items())
    entries.extend(EXCLUDED_TOOL_IDS.items())
    return entries
