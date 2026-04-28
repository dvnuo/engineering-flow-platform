from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class BundleTemplateDefinition:
    template_id: str
    display_name: str
    artifact_files: dict[str, str]
    compatible_task_template_ids: tuple[str, ...] = ()


_BUNDLE_TEMPLATE_REGISTRY: Dict[str, BundleTemplateDefinition] = {
    "requirement.v1": BundleTemplateDefinition(
        template_id="requirement.v1",
        display_name="Requirement Bundle",
        artifact_files={
            "requirements": "requirements.yaml",
            "test_cases": "test-cases.yaml",
        },
        compatible_task_template_ids=(
            "collect_requirements_to_bundle",
            "design_test_cases_from_bundle",
        ),
    ),
    "research.v1": BundleTemplateDefinition(
        template_id="research.v1",
        display_name="Research Bundle",
        artifact_files={
            "research_notes": "research-notes.yaml",
        },
        compatible_task_template_ids=("collect_research_notes_to_bundle",),
    ),
    "development.v1": BundleTemplateDefinition(
        template_id="development.v1",
        display_name="Development Bundle",
        artifact_files={
            "implementation_plan": "implementation-plan.yaml",
        },
        compatible_task_template_ids=("generate_implementation_plan_from_bundle",),
    ),
    "operations.v1": BundleTemplateDefinition(
        template_id="operations.v1",
        display_name="Operations Bundle",
        artifact_files={
            "runbook": "runbook.yaml",
        },
        compatible_task_template_ids=("generate_runbook_from_bundle",),
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


def resolve_bundle_template_id_from_manifest(manifest: Dict[str, Any]) -> str:
    template_id = str((manifest or {}).get("template_id") or "").strip().lower()
    if template_id:
        return require_bundle_template(template_id).template_id

    links = (manifest or {}).get("links")
    if isinstance(links, dict) and bool(links):
        return "requirement.v1"

    raise _bundle_error("bundle.yaml requires 'template_id' or legacy 'links'")
