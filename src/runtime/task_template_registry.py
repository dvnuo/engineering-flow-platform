from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from src.runtime.bundle_template_registry import get_bundle_template


@dataclass(frozen=True)
class TaskTemplateDefinition:
    template_id: str
    label: str
    task_type: str
    task_family: str
    provider: str | None = None
    default_trigger: str | None = None
    default_skill_name: str | None = None
    required_inputs: tuple[str, ...] = ()
    optional_inputs: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    compatible_bundle_templates: tuple[str, ...] = ()
    requires_bundle: bool = False
    requires_sources: bool = False


_TASK_TEMPLATE_REGISTRY: Dict[str, TaskTemplateDefinition] = {
    "collect_requirements_to_bundle": TaskTemplateDefinition(
        template_id="collect_requirements_to_bundle",
        label="Collect Requirements to Bundle",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="collect_requirements_to_bundle",
        compatible_bundle_templates=("requirement.v1",),
        requires_bundle=True,
        requires_sources=True,
        output_artifacts=("requirements",),
    ),
    "design_test_cases_from_bundle": TaskTemplateDefinition(
        template_id="design_test_cases_from_bundle",
        label="Design Test Cases from Bundle",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="design_test_cases_from_bundle",
        compatible_bundle_templates=("requirement.v1",),
        requires_bundle=True,
        output_artifacts=("test_cases",),
    ),
    "collect_research_notes_to_bundle": TaskTemplateDefinition(
        template_id="collect_research_notes_to_bundle",
        label="Collect Research Notes to Bundle",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="collect_research_notes_to_bundle",
        compatible_bundle_templates=("research.v1",),
        requires_bundle=True,
        requires_sources=True,
        output_artifacts=("research_notes",),
    ),
    "generate_implementation_plan_from_bundle": TaskTemplateDefinition(
        template_id="generate_implementation_plan_from_bundle",
        label="Generate Implementation Plan from Bundle",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="generate_implementation_plan_from_bundle",
        compatible_bundle_templates=("development.v1",),
        requires_bundle=True,
        output_artifacts=("implementation_plan",),
    ),
    "generate_runbook_from_bundle": TaskTemplateDefinition(
        template_id="generate_runbook_from_bundle",
        label="Generate Runbook from Bundle",
        task_type="bundle_action_task",
        task_family="bundle",
        default_skill_name="generate_runbook_from_bundle",
        compatible_bundle_templates=("operations.v1",),
        requires_bundle=True,
        output_artifacts=("runbook",),
    ),
    "github_pr_review": TaskTemplateDefinition(
        template_id="github_pr_review",
        label="GitHub PR Review",
        task_type="github_review_task",
        task_family="review",
        provider="github",
        default_trigger="github_pr_review_requested",
        default_skill_name="review-pull-request",
        required_inputs=("owner", "repo", "pull_number"),
        optional_inputs=(
            "review_event",
            "head_sha",
            "writeback_mode",
            "review_target",
            "review_target_type",
            "skill_name",
            "execution_mode",
        ),
    ),
    "github_comment_mention": TaskTemplateDefinition(
        template_id="github_comment_mention",
        label="GitHub Comment Mention",
        task_type="triggered_event_task",
        task_family="triggered_work",
        provider="github",
        default_trigger="github_comment_mention",
        default_skill_name="handle-triggered-event",
        required_inputs=("owner", "repo", "comment_id", "comment_kind", "body", "mentioned_account"),
        optional_inputs=(
            "issue_number",
            "pull_number",
            "review_comment_id",
            "in_reply_to_id",
            "commit_id",
            "commit_sha",
            "context_type",
            "source_kind",
            "source_event",
            "author",
            "author_association",
            "html_url",
            "path",
            "line",
            "position",
            "side",
            "diff_hunk",
            "mentioned_logins",
            "reply_mode",
            "session_id",
            "automation_rule_id",
            "rule_id",
            "dedupe_key",
            "skill_name",
            "execution_mode",
        ),
    ),
}


def list_task_templates() -> tuple[TaskTemplateDefinition, ...]:
    return tuple(_TASK_TEMPLATE_REGISTRY.values())


def get_task_template(template_id: str) -> TaskTemplateDefinition | None:
    normalized = str(template_id or "").strip().lower()
    if not normalized:
        return None
    return _TASK_TEMPLATE_REGISTRY.get(normalized)


def require_task_template(template_id: str) -> TaskTemplateDefinition:
    resolved = get_task_template(template_id)
    if resolved is None:
        raise ValueError(f"Unsupported task_template_id: {template_id}")
    return resolved


def resolve_task_template_from_payload(payload: Dict[str, Any]) -> TaskTemplateDefinition | None:
    normalized_payload = payload if isinstance(payload, dict) else {}

    # Preferred field for runtime task-template routing.
    task_template_id = str(normalized_payload.get("task_template_id") or "").strip().lower()
    if task_template_id:
        return get_task_template(task_template_id)

    # Fallback for legacy callers: only accept `template_id` if it is NOT a known
    # bundle template id (e.g. requirement.v1). `bundle_template_id` remains the
    # canonical bundle layout identifier.
    template_id = str(normalized_payload.get("template_id") or "").strip().lower()
    if not template_id:
        return None
    if get_bundle_template(template_id) is not None:
        return None
    return get_task_template(template_id)
