from __future__ import annotations

from pathlib import Path
from typing import Any

from efp_runtime.skills.discovery import SkillDiscovery
from efp_runtime.tools.builtin import WebSearchRequest, create_core_tool_registry
from efp_runtime.tools.builtin.task import TaskToolRequest
from tests._runtime_tool_surface_contract import (
    CAPABILITY_GROUPS,
    CONDITIONAL_TOOL_IDS,
    EXCLUDED_RUNTIME_SURFACES,
    EXPECTED_DEFAULT_CORE_TOOL_IDS,
    REMOVED_LEGACY_TOOL_IDS,
)


ALLOWED_STATUSES = {"implemented", "conditional", "excluded", "remaining"}


async def _task_runner(request: TaskToolRequest) -> str:
    return f"completed {request.description}"


def _websearch_runner(request: WebSearchRequest) -> str:
    return f"results for {request.query}"


def test_default_registry_matches_efp_expected_default_core_ids(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert tuple(registry.ids()) == EXPECTED_DEFAULT_CORE_TOOL_IDS
    assert REMOVED_LEGACY_TOOL_IDS.isdisjoint(registry.ids())
    assert set(CONDITIONAL_TOOL_IDS).isdisjoint(registry.ids())


def test_conditional_contract_tool_ids_are_registrable_under_conditions(
    tmp_path: Path,
):
    registry = create_core_tool_registry(
        tmp_path,
        task_runner=_task_runner,
        allow_background_task=True,
        include_question_tool=True,
        skill_discovery=SkillDiscovery([]),
        include_lsp_tool=True,
        include_plan_tool=True,
        include_repository_tools=True,
        websearch_runner=_websearch_runner,
    )

    assert "websearch" in CONDITIONAL_TOOL_IDS
    assert set(CONDITIONAL_TOOL_IDS).issubset(registry.ids())
    assert "mcp" not in registry.ids()


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
        "legacy boundary",
    }

    assert expected_groups.issubset(CAPABILITY_GROUPS)
    for name, entry in CAPABILITY_GROUPS.items():
        assert name.strip()
        assert entry.status in ALLOWED_STATUSES
        assert entry.summary.strip()


def test_session_state_contract_mentions_summary_and_revert():
    summary = CAPABILITY_GROUPS["session state"].summary

    assert "summary diffs" in summary
    assert "revert/unrevert" in summary


def test_legacy_boundary_contract_separates_import_independence_from_tree_removal():
    summary = CAPABILITY_GROUPS["legacy boundary"].summary

    assert "independent from legacy core imports" in summary
    assert "repository-level deletion" in summary
    assert "separate migration item" in summary


def test_contract_explicitly_excludes_mcp(tmp_path: Path):
    entry = EXCLUDED_RUNTIME_SURFACES["mcp"]

    assert entry.status == "excluded"
    assert "MCP" in entry.reason
    assert "mcp" not in create_core_tool_registry(tmp_path).ids()


def test_remaining_contract_items_have_concrete_next_actions():
    vague_actions = {"", "todo", "tbd", "follow up", "investigate", "later"}

    for name, entry in _contract_entries():
        if entry.status != "remaining":
            continue
        action = (entry.next_action or "").strip()
        assert action.lower() not in vague_actions, name
        assert len(action.split()) >= 8, name


def test_contract_has_no_remaining_items():
    assert [
        name for name, entry in _contract_entries() if entry.status == "remaining"
    ] == []


def _contract_entries() -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    entries.extend(CAPABILITY_GROUPS.items())
    entries.extend(CONDITIONAL_TOOL_IDS.items())
    entries.extend(EXCLUDED_RUNTIME_SURFACES.items())
    return entries
