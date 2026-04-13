from __future__ import annotations

import json
from typing import Any, Dict

from src.agents.executor import SkillResult, skill
from src.agents.llm import LLMClient
from src.runtime.requirement_bundle_assets import (
    RequirementBundleError,
    load_bundle_manifest,
    read_bundle_yaml,
    resolve_bundle_artifacts,
    resolve_target_bundle_ref,
    write_bundle_yaml,
)

from skills.collect_requirements_to_bundle.skill import _extract_json_dict


@skill(
    name="generate_implementation_plan_from_bundle",
    description="Generate implementation plan artifact from bundle context.",
)
async def generate_implementation_plan_from_bundle(
    bundle_ref: Dict[str, Any],
    manifest_ref: Dict[str, Any] | None = None,
) -> SkillResult:
    try:
        manifest_source_ref, manifest = await load_bundle_manifest(bundle_ref, manifest_ref=manifest_ref)
        target_ref = resolve_target_bundle_ref(manifest_source_ref, manifest)
        artifacts = resolve_bundle_artifacts(manifest)

        implementation_plan_file = str(artifacts.get("implementation_plan") or "").strip().strip("/")
        if not implementation_plan_file:
            raise RequirementBundleError("bundle.yaml field 'artifacts.implementation_plan' must be a non-empty string")

        requirements_doc: Dict[str, Any] | None = None
        requirements_file = str(artifacts.get("requirements") or "").strip().strip("/")
        if requirements_file:
            try:
                requirements_doc = await read_bundle_yaml(target_ref, requirements_file)
            except RequirementBundleError:
                requirements_doc = None

        prompt_context = {
            "bundle_id": manifest.get("bundle_id") or manifest.get("id") or target_ref.path,
            "template_id": manifest.get("template_id"),
            "title": manifest.get("title"),
            "scope": manifest.get("scope", {}),
            "metadata": manifest.get("metadata", {}),
            "artifacts": artifacts,
            "requirements": requirements_doc,
        }

        llm = LLMClient()
        llm_response = await llm.chat(
            system_prompt=(
                "You are an implementation planner. Return STRICT JSON only with top-level keys: "
                "summary, workstreams, tasks, risks, validation_checks."
            ),
            messages=[{"role": "user", "content": json.dumps(prompt_context, ensure_ascii=False)}],
            temperature=0.1,
        )
        structured = _extract_json_dict(str(llm_response.get("content") or ""))

        payload = {
            "bundle_id": manifest.get("bundle_id") or manifest.get("id") or target_ref.path,
            "generated_from_bundle_commit": "",
            "summary": structured.get("summary", {}),
            "workstreams": structured.get("workstreams", []),
            "tasks": structured.get("tasks", []),
            "risks": structured.get("risks", []),
            "validation_checks": structured.get("validation_checks", []),
        }

        write_result = await write_bundle_yaml(
            target_ref,
            implementation_plan_file,
            payload,
            f"chore(bundle): update {implementation_plan_file} for {target_ref.path}",
        )
        commit_sha = ((write_result.get("commit") or {}).get("sha")) if isinstance(write_result, dict) else None

        return SkillResult(
            success=True,
            output="implementation-plan.yaml updated",
            data={
                "bundle_ref": {
                    "repo": target_ref.repo_full_name,
                    "path": target_ref.path,
                    "branch": target_ref.branch,
                },
                "updated_files": [f"{target_ref.path}/{implementation_plan_file}"],
                "commit_sha": commit_sha,
                "summary": "Generated implementation plan from bundle context",
            },
        )
    except RequirementBundleError as exc:
        return SkillResult(success=False, error=str(exc))
    except Exception as exc:
        return SkillResult(success=False, error=f"generate_implementation_plan_from_bundle failed: {exc}")
