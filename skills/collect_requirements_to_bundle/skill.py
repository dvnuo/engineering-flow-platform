from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from src.agents.executor import SkillResult, skill
from src.agents.llm import LLMClient
from src.channels import confluence_channel, jira_channel
from src.github import github_channel
from src.runtime.requirement_bundle_assets import (
    RequirementBundleError,
    load_bundle_manifest,
    parse_bundle_ref,
    read_bundle_text,
    write_requirements_doc,
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


async def _load_jira_sources(issue_keys: List[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for issue_key in issue_keys:
        issue = await jira_channel.get_issue(issue_key)
        items.append({"issue_key": issue_key, "issue": issue})
    return items


async def _load_confluence_sources(page_ids: List[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for page_id in page_ids:
        page = await confluence_channel.get_page(page_id)
        items.append({"page_id": page_id, "page": page})
    return items


async def _load_github_doc_sources(bundle_ref: Dict[str, Any], doc_paths: List[str]) -> List[Dict[str, Any]]:
    parsed_ref = parse_bundle_ref(bundle_ref)
    items: List[Dict[str, Any]] = []
    for doc_path in doc_paths:
        raw = await read_bundle_text(parsed_ref, doc_path)
        items.append({"path": doc_path, "content": raw})
    return items


@skill(
    name="collect_requirements_to_bundle",
    description="Collect requirements from sources and write requirements.yaml in RequirementBundle.",
)
async def collect_requirements_to_bundle(bundle_ref: Dict[str, Any], sources: Dict[str, Any] | None = None) -> SkillResult:
    try:
        if not github_channel.is_configured():
            return SkillResult(success=False, error="GitHub integration is not configured")

        parsed_ref, manifest = await load_bundle_manifest(bundle_ref)
        normalized_sources = dict(sources or {})
        jira_ids = [str(item).strip() for item in normalized_sources.get("jira", []) if str(item).strip()]
        confluence_ids = [str(item).strip() for item in normalized_sources.get("confluence", []) if str(item).strip()]
        github_docs = [str(item).strip() for item in normalized_sources.get("github_docs", []) if str(item).strip()]
        figma_refs = [str(item).strip() for item in normalized_sources.get("figma", []) if str(item).strip()]

        jira_payload = await _load_jira_sources(jira_ids) if jira_ids else []
        confluence_payload = await _load_confluence_sources(confluence_ids) if confluence_ids else []
        github_payload = await _load_github_doc_sources(bundle_ref, github_docs) if github_docs else []

        prompt_context = {
            "bundle": {
                "bundle_id": manifest.get("bundle_id") or manifest.get("id") or parsed_ref.path,
                "scope": manifest.get("scope", {}),
                "summary": manifest.get("summary", {}),
            },
            "sources": {
                "jira": jira_payload,
                "confluence": confluence_payload,
                "github_docs": github_payload,
            },
        }

        llm = LLMClient()
        llm_response = await llm.chat(
            system_prompt=(
                "You are a requirements analyst. Return STRICT JSON only (no markdown, no prose) with keys: "
                "summary, functional_requirements, business_rules, acceptance_criteria, edge_cases, quality_flags. "
                "quality_flags must contain ambiguities, conflicts, missing_information arrays."
            ),
            messages=[{"role": "user", "content": json.dumps(prompt_context, ensure_ascii=False)}],
            temperature=0.1,
        )
        structured = _extract_json_dict(str(llm_response.get("content") or ""))

        requirements_doc = {
            "bundle_id": manifest.get("bundle_id") or manifest.get("id") or parsed_ref.path,
            "sources": {
                "jira": jira_ids,
                "confluence": confluence_ids,
                "github_docs": github_docs,
                "figma": figma_refs,
            },
            "summary": structured.get("summary", {}),
            "functional_requirements": structured.get("functional_requirements", []),
            "business_rules": structured.get("business_rules", []),
            "acceptance_criteria": structured.get("acceptance_criteria", []),
            "edge_cases": structured.get("edge_cases", []),
            "quality_flags": structured.get(
                "quality_flags",
                {"ambiguities": [], "conflicts": [], "missing_information": []},
            ),
        }

        write_result = await write_requirements_doc(bundle_ref, requirements_doc)
        commit_sha = ((write_result.get("commit") or {}).get("sha")) if isinstance(write_result, dict) else None
        warnings: List[str] = []
        if figma_refs:
            warnings.append("figma sources are ignored in MVP")

        return SkillResult(
            success=True,
            output="requirements.yaml updated",
            data={
                "bundle_ref": {
                    "repo": parsed_ref.repo_full_name,
                    "path": parsed_ref.path,
                    "branch": parsed_ref.branch,
                },
                "updated_files": [f"{parsed_ref.path}/requirements.yaml"],
                "commit_sha": commit_sha,
                "summary": "Collected bundle requirements from configured sources",
                "warnings": warnings,
            },
        )
    except RequirementBundleError as exc:
        return SkillResult(success=False, error=str(exc))
    except Exception as exc:
        return SkillResult(success=False, error=f"collect_requirements_to_bundle failed: {exc}")
