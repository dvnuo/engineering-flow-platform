from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class BundleActionDefinition:
    action_id: str
    skill_name: str
    requires_sources: bool = False
    required_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class BundleTemplateDefinition:
    template_id: str
    display_name: str
    artifact_files: dict[str, str]
    actions: dict[str, BundleActionDefinition]


_BUNDLE_TEMPLATE_REGISTRY: Dict[str, BundleTemplateDefinition] = {
    "requirement.v1": BundleTemplateDefinition(
        template_id="requirement.v1",
        display_name="Requirement Bundle",
        artifact_files={
            "requirements": "requirements.yaml",
            "test_cases": "test-cases.yaml",
        },
        actions={
            "collect_requirements": BundleActionDefinition(
                action_id="collect_requirements",
                skill_name="collect_requirements_to_bundle",
                requires_sources=True,
            ),
            "design_test_cases": BundleActionDefinition(
                action_id="design_test_cases",
                skill_name="design_test_cases_from_bundle",
                required_artifacts=("requirements",),
            ),
        },
    ),
    "research.v1": BundleTemplateDefinition(
        template_id="research.v1",
        display_name="Research Bundle",
        artifact_files={
            "research_notes": "research-notes.yaml",
        },
        actions={
            "collect_research_notes": BundleActionDefinition(
                action_id="collect_research_notes",
                skill_name="collect_research_notes_to_bundle",
                requires_sources=True,
            ),
        },
    ),
    "development.v1": BundleTemplateDefinition(
        template_id="development.v1",
        display_name="Development Bundle",
        artifact_files={
            "implementation_plan": "implementation-plan.yaml",
        },
        actions={
            "generate_implementation_plan": BundleActionDefinition(
                action_id="generate_implementation_plan",
                skill_name="generate_implementation_plan_from_bundle",
            ),
        },
    ),
    "operations.v1": BundleTemplateDefinition(
        template_id="operations.v1",
        display_name="Operations Bundle",
        artifact_files={
            "runbook": "runbook.yaml",
        },
        actions={
            "generate_runbook": BundleActionDefinition(
                action_id="generate_runbook",
                skill_name="generate_runbook_from_bundle",
            ),
        },
    ),
}


def _bundle_error(message: str) -> Exception:
    from src.runtime.requirement_bundle_assets import RequirementBundleError

    return RequirementBundleError(message)


def get_bundle_template(template_id: str) -> BundleTemplateDefinition | None:
    normalized = str(template_id or "").strip().lower()
    if not normalized:
        return None
    return _BUNDLE_TEMPLATE_REGISTRY.get(normalized)


def require_bundle_template(template_id: str) -> BundleTemplateDefinition:
    resolved = get_bundle_template(template_id)
    if resolved is None:
        raise _bundle_error(f"Unsupported bundle template_id: {template_id}")
    return resolved


def get_bundle_action(template_id: str, action_id: str) -> BundleActionDefinition | None:
    template = get_bundle_template(template_id)
    if template is None:
        return None
    normalized_action_id = str(action_id or "").strip().lower()
    if not normalized_action_id:
        return None
    return template.actions.get(normalized_action_id)


def require_bundle_action(template_id: str, action_id: str) -> BundleActionDefinition:
    action = get_bundle_action(template_id, action_id)
    if action is None:
        raise _bundle_error(f"Unsupported bundle action '{action_id}' for template '{template_id}'")
    return action


def resolve_bundle_template_id_from_manifest(manifest: Dict[str, Any]) -> str:
    template_id = str((manifest or {}).get("template_id") or "").strip().lower()
    if template_id:
        return require_bundle_template(template_id).template_id

    links = (manifest or {}).get("links")
    if isinstance(links, dict) and bool(links):
        return "requirement.v1"

    raise _bundle_error("bundle.yaml requires 'template_id' or legacy 'links'")
