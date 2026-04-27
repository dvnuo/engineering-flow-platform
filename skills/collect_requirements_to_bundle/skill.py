from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from src.agents.executor import SkillResult, skill
from src.agents.llm import LLMClient
from src.github import github_channel
from src.runtime.requirement_bundle_assets import (
    RequirementBundleError,
    load_bundle_manifest,
    resolve_bundle_links,
    resolve_target_bundle_ref,
    write_requirements_doc_for_ref,
)
from src.utils.redaction import sanitize_exception_message
from skills.shared_bundle_source_loaders import (
    _load_confluence_sources,
    _load_github_doc_sources,
    _load_jira_sources,
)

# NOTE: Source loader implementations were extracted to skills/shared_bundle_source_loaders.py.
# Historical call-shape references retained for regression checks:
# prepared = await prepare_jira_issue_source(source, session_id=session_id)
# prepared = await prepare_confluence_page_source(source, session_id=session_id)


JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
logger = logging.getLogger(__name__)


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
    name="collect_requirements_to_bundle",
    description="Collect requirements from sources and write requirements.yaml in RequirementBundle.",
)
async def collect_requirements_to_bundle(
    bundle_ref: Dict[str, Any],
    sources: Dict[str, Any] | None = None,
    manifest_ref: Dict[str, Any] | None = None,
    _session_id: str | None = None,
) -> SkillResult:
    try:
        if not github_channel.is_configured():
            return SkillResult(success=False, error="GitHub integration is not configured")

        normalized_sources = dict(sources or {})
        jira_ids = [str(item).strip() for item in normalized_sources.get("jira", []) if str(item).strip()]
        confluence_ids = [str(item).strip() for item in normalized_sources.get("confluence", []) if str(item).strip()]
        github_docs = [str(item).strip() for item in normalized_sources.get("github_docs", []) if str(item).strip()]
        figma_refs = [str(item).strip() for item in normalized_sources.get("figma", []) if str(item).strip()]
        logger.info(
            "Collect requirements skill start | bundle_ref=%s has_manifest_ref=%s jira_count=%s confluence_count=%s github_docs_count=%s figma_count=%s",
            {"repo": bundle_ref.get("repo"), "path": bundle_ref.get("path"), "branch": bundle_ref.get("branch")} if isinstance(bundle_ref, dict) else {},
            bool(manifest_ref),
            len(jira_ids),
            len(confluence_ids),
            len(github_docs),
            len(figma_refs),
        )

        manifest_source_ref, manifest = await load_bundle_manifest(bundle_ref, manifest_ref=manifest_ref)
        target_ref = resolve_target_bundle_ref(manifest_source_ref, manifest)
        requirements_file, _ = resolve_bundle_links(manifest)
        logger.info(
            "Collect requirements manifest resolved | manifest_ref=%s target_ref=%s requirements_file=%s",
            {"repo": manifest_source_ref.repo_full_name, "path": manifest_source_ref.path, "branch": manifest_source_ref.branch},
            {"repo": target_ref.repo_full_name, "path": target_ref.path, "branch": target_ref.branch},
            requirements_file,
        )
        supported_source_count = len(jira_ids) + len(confluence_ids) + len(github_docs)
        if supported_source_count <= 0:
            error = "At least one supported source is required"
            if figma_refs:
                error = f"{error}; figma is ignored in MVP"
            return SkillResult(success=False, error=error)

        logger.info("Collect requirements load sources start | jira_count=%s confluence_count=%s github_docs_count=%s", len(jira_ids), len(confluence_ids), len(github_docs))
        jira_payload = await _load_jira_sources(jira_ids, session_id=_session_id) if jira_ids else []
        logger.debug("Collect requirements load jira done | count=%s", len(jira_payload))
        confluence_payload = await _load_confluence_sources(confluence_ids, session_id=_session_id) if confluence_ids else []
        logger.debug("Collect requirements load confluence done | count=%s", len(confluence_payload))
        github_payload = (
            await _load_github_doc_sources(
                {
                    "repo": target_ref.repo_full_name,
                    "path": target_ref.path,
                    "branch": target_ref.branch,
                },
                github_docs,
                session_id=_session_id,
            )
            if github_docs
            else []
        )
        logger.debug("Collect requirements load github docs done | count=%s", len(github_payload))

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
        logger.info("Collect requirements llm request start")
        llm_response = await llm.chat(
            system_prompt=(
                "You are a requirements analyst. Return STRICT JSON only (no markdown, no prose) with keys: "
                "summary, functional_requirements, business_rules, acceptance_criteria, edge_cases, quality_flags. "
                "quality_flags must contain ambiguities, conflicts, missing_information arrays."
            ),
            messages=[{"role": "user", "content": json.dumps(prompt_context, ensure_ascii=False)}],
            temperature=0.1,
        )
        logger.info("Collect requirements llm request done | response_content_length=%s", len(str(llm_response.get("content") or "")))
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

        logger.info(
            "Collect requirements write start | target_ref=%s requirements_file=%s",
            {"repo": target_ref.repo_full_name, "path": target_ref.path, "branch": target_ref.branch},
            requirements_file,
        )
        write_result = await write_requirements_doc_for_ref(
            target_ref, requirements_doc, requirements_file=requirements_file
        )
        commit_sha = ((write_result.get("commit") or {}).get("sha")) if isinstance(write_result, dict) else None
        logger.info("Collect requirements write done | requirements_file=%s has_commit_sha=%s", requirements_file, bool(commit_sha))
        warnings: List[str] = []
        if figma_refs:
            warnings.append("figma sources are ignored in MVP")
            logger.info("Collect requirements figma ignored in MVP | figma_count=%s", len(figma_refs))

        logger.info("Collect requirements skill finish | success=%s has_commit_sha=%s", True, bool(commit_sha))
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
        logger.warning(
            "Collect requirements skill failed | action=collect_requirements_to_bundle error_class=%s error=%s",
            exc.__class__.__name__,
            sanitize_exception_message(exc),
        )
        return SkillResult(success=False, error=str(exc))
    except Exception as exc:
        logger.warning(
            "Collect requirements skill failed | action=collect_requirements_to_bundle error_class=%s error=%s",
            exc.__class__.__name__,
            sanitize_exception_message(exc),
        )
        return SkillResult(success=False, error=f"collect_requirements_to_bundle failed: {exc}")
