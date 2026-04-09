from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from src.agents.executor import SkillResult, skill
from src.agents.llm import LLMClient
from src.github import github_channel
from src.jira import jira_get_issue, jira_get_issue_by_url
from src.confluence import confluence_get_page, confluence_get_page_by_url
from src.runtime.requirement_bundle_assets import (
    RequirementBundleError,
    load_bundle_manifest,
    parse_bundle_ref,
    read_github_doc_text,
    resolve_bundle_links,
    resolve_target_bundle_ref,
    write_requirements_doc_for_ref,
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
    for source in issue_keys:
        is_url = "://" in source and "/browse/" in source.lower()
        rendered = await (jira_get_issue_by_url(source) if is_url else jira_get_issue(source))
        items.append({"input": source, "kind": "url" if is_url else "issue_key", "content": str(rendered or "")})
    return items


async def _load_confluence_sources(page_ids: List[str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for source in page_ids:
        is_url = "://" in source
        rendered = await (confluence_get_page_by_url(source) if is_url else confluence_get_page(source))
        items.append({"input": source, "kind": "url" if is_url else "page_id", "content": str(rendered or "")})
    return items


async def _load_github_doc_sources(bundle_ref: Dict[str, Any], doc_paths: List[str]) -> List[Dict[str, Any]]:
    parsed_ref = parse_bundle_ref(bundle_ref)
    items: List[Dict[str, Any]] = []
    for doc_path in doc_paths:
        doc_ref, raw = await read_github_doc_text(doc_path, parsed_ref)
        kind = "url" if "://" in doc_path else "repo_relative_path"
        items.append(
            {
                "input": doc_path,
                "kind": kind,
                "resolved": {
                    "owner": doc_ref.owner,
                    "repo": doc_ref.repo,
                    "branch": doc_ref.branch,
                    "path": doc_ref.path,
                },
                "content": raw,
            }
        )
    return items


@skill(
    name="collect_requirements_to_bundle",
    description="Collect requirements from sources and write requirements.yaml in RequirementBundle.",
)
async def collect_requirements_to_bundle(bundle_ref: Dict[str, Any], sources: Dict[str, Any] | None = None) -> SkillResult:
    try:
        if not github_channel.is_configured():
            return SkillResult(success=False, error="GitHub integration is not configured")

        input_ref, manifest = await load_bundle_manifest(bundle_ref)
        target_ref = resolve_target_bundle_ref(input_ref, manifest)
        requirements_file, _ = resolve_bundle_links(manifest)
        normalized_sources = dict(sources or {})
        jira_ids = [str(item).strip() for item in normalized_sources.get("jira", []) if str(item).strip()]
        confluence_ids = [str(item).strip() for item in normalized_sources.get("confluence", []) if str(item).strip()]
        github_docs = [str(item).strip() for item in normalized_sources.get("github_docs", []) if str(item).strip()]
        figma_refs = [str(item).strip() for item in normalized_sources.get("figma", []) if str(item).strip()]
        supported_source_count = len(jira_ids) + len(confluence_ids) + len(github_docs)
        if supported_source_count <= 0:
            error = "At least one supported source is required"
            if figma_refs:
                error = f"{error}; figma is ignored in MVP"
            return SkillResult(success=False, error=error)

        jira_payload = await _load_jira_sources(jira_ids) if jira_ids else []
        confluence_payload = await _load_confluence_sources(confluence_ids) if confluence_ids else []
        github_payload = (
            await _load_github_doc_sources(
                {
                    "repo": target_ref.repo_full_name,
                    "path": target_ref.path,
                    "branch": target_ref.branch,
                },
                github_docs,
            )
            if github_docs
            else []
        )

        prompt_context = {
            "bundle": {
                "bundle_id": manifest.get("bundle_id") or manifest.get("id") or target_ref.path,
                "scope": manifest.get("scope", {}),
                "summary": (manifest.get("scope", {}) or {}).get("summary", ""),
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
            "bundle_id": manifest.get("bundle_id") or manifest.get("id") or target_ref.path,
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

        write_result = await write_requirements_doc_for_ref(
            target_ref, requirements_doc, requirements_file=requirements_file
        )
        commit_sha = ((write_result.get("commit") or {}).get("sha")) if isinstance(write_result, dict) else None
        warnings: List[str] = []
        if figma_refs:
            warnings.append("figma sources are ignored in MVP")

        return SkillResult(
            success=True,
            output="requirements.yaml updated",
            data={
                "bundle_ref": {
                    "repo": target_ref.repo_full_name,
                    "path": target_ref.path,
                    "branch": target_ref.branch,
                },
                "updated_files": [f"{target_ref.path}/{requirements_file}"],
                "commit_sha": commit_sha,
                "summary": "Collected bundle requirements from configured sources",
                "warnings": warnings,
            },
        )
    except RequirementBundleError as exc:
        return SkillResult(success=False, error=str(exc))
    except Exception as exc:
        return SkillResult(success=False, error=f"collect_requirements_to_bundle failed: {exc}")
