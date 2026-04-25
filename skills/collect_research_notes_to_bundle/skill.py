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
    resolve_bundle_artifacts,
    resolve_target_bundle_ref,
    write_bundle_yaml,
)
from src.utils.redaction import sanitize_exception_message

from skills.shared_bundle_source_loaders import (
    _load_confluence_sources,
    _load_github_doc_sources,
    _load_jira_sources,
)

logger = logging.getLogger(__name__)
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
    name="collect_research_notes_to_bundle",
    description="Collect research notes from sources and write research-notes.yaml in bundle.",
)
async def collect_research_notes_to_bundle(
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

        manifest_source_ref, manifest = await load_bundle_manifest(bundle_ref, manifest_ref=manifest_ref)
        target_ref = resolve_target_bundle_ref(manifest_source_ref, manifest)
        artifacts = resolve_bundle_artifacts(manifest)
        research_notes_file = str(artifacts.get("research_notes") or "").strip().strip("/")
        if not research_notes_file:
            raise RequirementBundleError("bundle.yaml field 'artifacts.research_notes' must be a non-empty string")

        supported_source_count = len(jira_ids) + len(confluence_ids) + len(github_docs)
        if supported_source_count <= 0:
            error = "At least one supported source is required"
            if figma_refs:
                error = f"{error}; figma is ignored in MVP"
            return SkillResult(success=False, error=error)

        jira_payload = await _load_jira_sources(jira_ids, session_id=_session_id) if jira_ids else []
        confluence_payload = await _load_confluence_sources(confluence_ids, session_id=_session_id) if confluence_ids else []
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

        prompt_context = {
            "bundle": {
                "bundle_id": manifest.get("bundle_id") or manifest.get("id") or target_ref.path,
                "template_id": manifest.get("template_id"),
                "title": manifest.get("title"),
                "scope": manifest.get("scope", {}),
                "metadata": manifest.get("metadata", {}),
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
                "You are a research analyst. Return STRICT JSON only (no markdown, no prose) with top-level keys: "
                "summary, findings, open_questions, references."
            ),
            messages=[{"role": "user", "content": json.dumps(prompt_context, ensure_ascii=False)}],
            temperature=0.1,
        )
        structured = _extract_json_dict(str(llm_response.get("content") or ""))

        research_doc = {
            "bundle_id": manifest.get("bundle_id") or manifest.get("id") or target_ref.path,
            "sources": {
                "jira": jira_ids,
                "confluence": confluence_ids,
                "github_docs": github_docs,
                "figma": figma_refs,
            },
            "summary": structured.get("summary", {}),
            "findings": structured.get("findings", []),
            "open_questions": structured.get("open_questions", []),
            "references": structured.get("references", []),
        }

        write_result = await write_bundle_yaml(
            target_ref,
            research_notes_file,
            research_doc,
            f"chore(bundle): update {research_notes_file} for {target_ref.path}",
        )
        commit_sha = ((write_result.get("commit") or {}).get("sha")) if isinstance(write_result, dict) else None
        warnings: List[str] = []
        if figma_refs:
            warnings.append("figma sources are ignored in MVP")

        data = {
            "bundle_ref": {
                "repo": target_ref.repo_full_name,
                "path": target_ref.path,
                "branch": target_ref.branch,
            },
            "updated_files": [f"{target_ref.path}/{research_notes_file}"],
            "commit_sha": commit_sha,
            "summary": "Collected research notes from configured sources",
        }
        if warnings:
            data["warnings"] = warnings

        return SkillResult(success=True, output="research-notes.yaml updated", data=data)
    except RequirementBundleError as exc:
        logger.warning(
            "Collect research notes skill failed | action=collect_research_notes_to_bundle error_class=%s error=%s",
            exc.__class__.__name__,
            sanitize_exception_message(exc),
        )
        return SkillResult(success=False, error=str(exc))
    except Exception as exc:
        logger.warning(
            "Collect research notes skill failed | action=collect_research_notes_to_bundle error_class=%s error=%s",
            exc.__class__.__name__,
            sanitize_exception_message(exc),
        )
        return SkillResult(success=False, error=f"collect_research_notes_to_bundle failed: {exc}")
