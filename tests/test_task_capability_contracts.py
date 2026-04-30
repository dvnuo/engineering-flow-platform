from src.runtime.execution_bus import resolve_task_capability_plan
from src.runtime.task_capability_contracts import resolve_task_capability_contract


def test_resolve_task_capability_contract_adapter_action_task_known_action():
    plan = resolve_task_capability_contract("adapter_action_task", {"action_id": "ADAPTER:GITHUB:ADD_COMMENT"})

    assert plan["primary_capability_id"] == "adapter:github:add_comment"
    assert plan["capability_id"] == "adapter:github:add_comment"
    assert plan["capability_type"] == "adapter_action"
    assert plan["action_id"] == "adapter:github:add_comment"
    assert "adapter:github:add_comment" in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_adapter_action_task_unknown_action_structurally_complete():
    plan = resolve_task_capability_contract("adapter_action_task", {"action_id": "ADAPTER:GITHUB:UNKNOWN_REVIEW"})

    assert plan["primary_capability_id"] == "adapter:github:unknown_review"
    assert plan["capability_id"] == "adapter:github:unknown_review"
    assert plan["action_id"] == "adapter:github:unknown_review"
    assert plan["capability_type"] == "adapter_action"
    assert plan["involved_capability_ids"] == ["adapter:github:unknown_review"]
    assert plan["capability_resolution"] == "unresolved"


def test_resolve_task_capability_contract_jira_workflow_review_task():
    plan = resolve_task_capability_contract(
        "jira_workflow_review_task",
        {
            "issue_key": "ENG-1",
            "success_transition": "Done",
            "assignee": "user-a",
            "review_comment": "looks good",
            "fields": {"priority": "High"},
        },
    )

    assert plan["primary_capability_id"] == "adapter:jira:read_issue"
    assert plan["capability_id"] == "adapter:jira:read_issue"
    assert plan["action_id"] == "adapter:jira:read_issue"
    assert plan["involved_capability_ids"] == [
        "adapter:jira:add_comment",
        "adapter:jira:assign_issue",
        "adapter:jira:read_issue",
        "adapter:jira:transition_issue",
        "adapter:jira:update_issue",
    ]


def test_resolve_task_capability_contract_jira_workflow_review_task_unresolved_sets_adapter_action_type(monkeypatch):
    from src.runtime import task_capability_contracts as module

    class _Registry:
        def get(self, capability_id):
            if capability_id == "adapter:jira:read_issue":
                return None
            return None

    monkeypatch.setattr(module, "get_capability_registry", lambda: _Registry())

    plan = module.resolve_task_capability_contract("jira_workflow_review_task", {"issue_key": "ENG-1"})

    assert plan["primary_capability_id"] == "adapter:jira:read_issue"
    assert plan["capability_id"] == "adapter:jira:read_issue"
    assert plan["action_id"] == "adapter:jira:read_issue"
    assert plan["capability_type"] == "adapter_action"
    assert plan["capability_resolution"] == "unresolved"


def test_resolve_task_capability_contract_jira_workflow_review_task_fields_on_success_triggers_update():
    plan = resolve_task_capability_contract(
        "jira_workflow_review_task",
        {
            "issue_key": "ENG-2",
            "fields_on_success": {"summary": "Approved"},
        },
    )

    assert "adapter:jira:update_issue" in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_jira_workflow_review_task_fields_on_failure_triggers_update():
    plan = resolve_task_capability_contract(
        "jira_workflow_review_task",
        {
            "issue_key": "ENG-3",
            "fields_on_failure": {"summary": "Rejected"},
        },
    )

    assert "adapter:jira:update_issue" in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_jira_workflow_review_task_empty_fields_on_outcomes_do_not_trigger_update():
    plan = resolve_task_capability_contract(
        "jira_workflow_review_task",
        {
            "issue_key": "ENG-4",
            "fields_on_success": {},
            "fields_on_failure": {},
        },
    )

    assert "adapter:jira:update_issue" not in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_jira_workflow_review_task_invalid_fields_on_outcomes_do_not_trigger_update():
    plan = resolve_task_capability_contract(
        "jira_workflow_review_task",
        {
            "issue_key": "ENG-5",
            "fields_on_success": "x",
            "fields_on_failure": ["y"],
        },
    )

    assert "adapter:jira:update_issue" not in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_github_review_task():
    plan = resolve_task_capability_contract("github_review_task", {"owner": "acme", "repo": "demo", "pull_number": 7})

    assert plan["primary_capability_id"] == "skill:review-pull-request"
    assert plan["capability_id"] == "skill:review-pull-request"
    assert plan["action_id"] == "skill:review-pull-request"
    assert plan["involved_capability_ids"] == ["adapter:github:review_pull_request", "skill:review-pull-request"]


def test_resolve_task_capability_contract_github_review_task_issue_comment_fallback():
    plan = resolve_task_capability_contract(
        "github_review_task",
        {"owner": "acme", "repo": "demo", "pull_number": 7, "writeback_mode": "issue_comment"},
    )

    assert plan["primary_capability_id"] == "skill:review-pull-request"
    assert plan["involved_capability_ids"] == ["adapter:github:add_comment", "skill:review-pull-request"]


def test_resolve_task_capability_contract_delegation_task():
    plan = resolve_task_capability_contract("delegation_task", {"skill_name": "Demo"})

    assert plan["primary_capability_id"] == "skill:demo"
    assert plan["capability_id"] == "skill:demo"
    assert plan["action_id"] == "skill:demo"
    assert plan["capability_type"] == "skill"
    assert plan["involved_capability_ids"] == ["skill:demo"]


def test_resolve_task_capability_contract_delegation_task_unresolved_sets_action_id():
    plan = resolve_task_capability_contract("delegation_task", {"skill_name": "Missing"})

    assert plan["primary_capability_id"] == "skill:missing"
    assert plan["capability_id"] == "skill:missing"
    assert plan["action_id"] == "skill:missing"
    assert plan["capability_type"] == "skill"
    assert plan["involved_capability_ids"] == ["skill:missing"]
    assert plan["capability_resolution"] == "unresolved"


def test_execution_bus_resolve_task_capability_plan_delegates_to_canonical_contract(monkeypatch):
    sentinel = {"primary_capability_id": "x", "capability_resolution": "resolved"}

    def _fake(task_type, payload):
        assert task_type == "adapter_action_task"
        assert payload == {"action_id": "adapter:github:add_comment"}
        return sentinel

    monkeypatch.setattr("src.runtime.execution_bus.resolve_task_capability_contract", _fake)

    plan = resolve_task_capability_plan("adapter_action_task", {"action_id": "adapter:github:add_comment"})

    assert plan is sentinel


def test_resolve_task_capability_contract_bundle_action_task_requirement_collect():
    plan = resolve_task_capability_contract(
        "bundle_action_task",
        {"task_template_id": "collect_requirements_to_bundle", "bundle_template_id": "requirement.v1"},
    )
    assert plan["primary_capability_id"] == "skill:collect_requirements_to_bundle"
    assert plan["capability_id"] == "skill:collect_requirements_to_bundle"


def test_resolve_task_capability_contract_bundle_action_task_requirement_design():
    plan = resolve_task_capability_contract(
        "bundle_action_task",
        {"task_template_id": "design_test_cases_from_bundle", "bundle_template_id": "requirement.v1"},
    )
    assert plan["primary_capability_id"] == "skill:design_test_cases_from_bundle"
    assert plan["capability_id"] == "skill:design_test_cases_from_bundle"


def test_resolve_task_capability_contract_bundle_action_task_research_collect():
    plan = resolve_task_capability_contract(
        "bundle_action_task",
        {"task_template_id": "collect_research_notes_to_bundle", "bundle_template_id": "research.v1"},
    )
    assert plan["primary_capability_id"] == "skill:collect_research_notes_to_bundle"
    assert plan["capability_id"] == "skill:collect_research_notes_to_bundle"


def test_resolve_task_capability_contract_bundle_action_task_development_generate():
    plan = resolve_task_capability_contract(
        "bundle_action_task",
        {"task_template_id": "generate_implementation_plan_from_bundle", "bundle_template_id": "development.v1"},
    )
    assert plan["primary_capability_id"] == "skill:generate_implementation_plan_from_bundle"
    assert plan["capability_id"] == "skill:generate_implementation_plan_from_bundle"


def test_resolve_task_capability_contract_bundle_action_task_operations_generate():
    plan = resolve_task_capability_contract(
        "bundle_action_task",
        {"task_template_id": "generate_runbook_from_bundle", "bundle_template_id": "operations.v1"},
    )
    assert plan["primary_capability_id"] == "skill:generate_runbook_from_bundle"
    assert plan["capability_id"] == "skill:generate_runbook_from_bundle"


def test_resolve_task_capability_contract_bundle_action_task_unknown_is_unresolved():
    plan = resolve_task_capability_contract(
        "bundle_action_task",
        {"task_template_id": "unknown_template"},
    )
    assert plan["capability_resolution"] == "unresolved"


def test_resolve_task_capability_contract_triggered_event_task_github_mention():
    plan = resolve_task_capability_contract("triggered_event_task", {"source_kind": "github.mention"})
    assert plan["primary_capability_id"] == "skill:handle-triggered-event"
    assert "adapter:github:add_comment" in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_triggered_event_task_jira_assigned():
    plan = resolve_task_capability_contract("triggered_event_task", {"source_kind": "jira.assigned"})
    assert plan["primary_capability_id"] == "skill:handle-triggered-event"
    assert "adapter:jira:add_comment" in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_triggered_event_task_confluence_mention():
    plan = resolve_task_capability_contract("triggered_event_task", {"source_kind": "confluence.mention"})
    assert plan["primary_capability_id"] == "skill:handle-triggered-event"
    assert "channel_action:confluence_add_comment" in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_triggered_event_task_github_review_comment_reply():
    plan = resolve_task_capability_contract("triggered_event_task", {"source_kind": "github.mention", "comment_kind": "pull_request_review_comment", "reply_mode": "same_surface"})
    assert "adapter:github:reply_review_comment" in plan["involved_capability_ids"]
    assert "adapter:github:add_comment" not in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_triggered_event_task_github_review_comment_timeline_fallback():
    plan = resolve_task_capability_contract("triggered_event_task", {"source_kind": "github.mention", "comment_kind": "pull_request_review_comment", "reply_mode": "timeline"})
    assert "adapter:github:add_comment" in plan["involved_capability_ids"]


def test_resolve_task_capability_contract_triggered_event_task_github_unsupported_comment_kind_no_add_comment():
    plan = resolve_task_capability_plan(
        "triggered_event_task",
        {"source_kind": "github.mention", "comment_kind": "commit_comment"},
    )
    assert "skill:handle-triggered-event" in plan["involved_capability_ids"]
    assert "adapter:github:add_comment" not in plan["involved_capability_ids"]
    assert "adapter:github:reply_review_comment" not in plan["involved_capability_ids"]
