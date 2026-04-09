from __future__ import annotations

import json
import re
from typing import Any, Dict

from src.agents.executor import SkillResult, skill
from src.agents.llm import LLMClient
from src.runtime.requirement_bundle_assets import (
    RequirementBundleError,
    build_test_design_context,
    load_bundle_manifest,
    load_requirements_doc_for_ref,
    resolve_bundle_links,
    resolve_target_bundle_ref,
    write_test_cases_doc_for_ref,
)

JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _strip_json_fence(text: str) -> str:
    match = JSON_FENCE_RE.match(text or "")
    return (match.group(1) if match else (text or "")).strip()


def _extract_json_dict(raw: str) -> Dict[str, Any]:
    cleaned = _strip_json_fence(raw)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise RequirementBundleError("LLM output must be a JSON object")
    return parsed


@skill(
    name="design_test_cases_from_bundle",
    description="Generate test-cases.yaml from RequirementBundle requirements.yaml",
)
async def design_test_cases_from_bundle(
    bundle_ref: Dict[str, Any],
    manifest_ref: Dict[str, Any] | None = None,
) -> SkillResult:
    try:
        manifest_source_ref, manifest = await load_bundle_manifest(bundle_ref, manifest_ref=manifest_ref)
        target_ref = resolve_target_bundle_ref(manifest_source_ref, manifest)
        requirements_file, test_cases_file = resolve_bundle_links(manifest)
        requirements_doc = await load_requirements_doc_for_ref(target_ref, requirements_file=requirements_file)
        designable_fields = (
            requirements_doc.get("functional_requirements") or [],
            requirements_doc.get("business_rules") or [],
            requirements_doc.get("acceptance_criteria") or [],
            requirements_doc.get("edge_cases") or [],
        )
        if not any(bool(field) for field in designable_fields):
            return SkillResult(
                success=False,
                error="requirements.yaml does not contain any designable requirement content",
            )
        design_context = build_test_design_context(manifest, requirements_doc)

        llm = LLMClient()
        llm_response = await llm.chat(
            system_prompt=(
                "You are a test designer. Return STRICT JSON only with top-level key test_cases as an array. "
                "Every test case must include: case_id, title, category, priority, preconditions, steps, "
                "expected_results, traceability."
            ),
            messages=[{"role": "user", "content": json.dumps(design_context, ensure_ascii=False)}],
            temperature=0.1,
        )
        structured = _extract_json_dict(str(llm_response.get("content") or ""))

        payload = {
            "bundle_id": manifest.get("bundle_id") or manifest.get("id") or target_ref.path,
            "generated_from_requirements_commit": "",
            "test_cases": structured.get("test_cases", []),
        }
        write_result = await write_test_cases_doc_for_ref(target_ref, payload, test_cases_file=test_cases_file)
        commit_sha = ((write_result.get("commit") or {}).get("sha")) if isinstance(write_result, dict) else None

        return SkillResult(
            success=True,
            output="test-cases.yaml updated",
            data={
                "bundle_ref": {
                    "repo": target_ref.repo_full_name,
                    "path": target_ref.path,
                    "branch": target_ref.branch,
                },
                "updated_files": [f"{target_ref.path}/{test_cases_file}"],
                "commit_sha": commit_sha,
                "summary": "Designed test cases from requirement bundle context",
            },
        )
    except RequirementBundleError as exc:
        return SkillResult(success=False, error=str(exc))
    except Exception as exc:
        return SkillResult(success=False, error=f"design_test_cases_from_bundle failed: {exc}")
