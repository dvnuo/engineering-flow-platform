from __future__ import annotations

from typing import Any, Dict, List


async def _load_jira_sources(issue_keys: List[str], *, session_id: str | None = None) -> List[Dict[str, Any]]:
    from src.jira.source_service import format_jira_source_manifest, prepare_jira_issue_source

    items: List[Dict[str, Any]] = []
    for source in issue_keys:
        is_url = "://" in source and "/browse/" in source.lower()
        prepared = await prepare_jira_issue_source(source, session_id=session_id)
        items.append(
            {
                "input": source,
                "kind": "url" if is_url else "issue_key",
                "source_kind": "jira_issue",
                "resolved": {"issue_key": prepared.issue_key},
                "content": format_jira_source_manifest(prepared),
                "artifact_refs": prepared.bundle.get("artifact_refs") or [],
                "context_ref": prepared.manifest.get("context_ref"),
                "digest_ref": prepared.manifest.get("digest_ref"),
            }
        )
    return items


async def _load_confluence_sources(page_ids: List[str], *, session_id: str | None = None) -> List[Dict[str, Any]]:
    from src.confluence.source_service import format_confluence_source_manifest, prepare_confluence_page_source

    items: List[Dict[str, Any]] = []
    for source in page_ids:
        is_url = "://" in source
        prepared = await prepare_confluence_page_source(source, session_id=session_id)
        manifest = prepared.get("manifest") or {}
        items.append(
            {
                "input": source,
                "kind": "url" if is_url else "page_id",
                "source_kind": "confluence_page",
                "resolved": {"page_id": prepared.get("page_id")},
                "content": format_confluence_source_manifest(prepared),
                "artifact_refs": prepared.get("artifact_refs") or [],
                "context_ref": manifest.get("context_ref"),
                "digest_ref": manifest.get("digest_ref"),
            }
        )
    return items


async def _load_github_doc_sources(
    bundle_ref: Dict[str, Any],
    doc_paths: List[str],
    *,
    session_id: str | None = None,
) -> List[Dict[str, Any]]:
    from src.runtime.requirement_bundle_assets import parse_bundle_ref, prepare_github_doc_source

    parsed_ref = parse_bundle_ref(bundle_ref)
    items: List[Dict[str, Any]] = []
    for doc_path in doc_paths:
        prepared = await prepare_github_doc_source(doc_path, parsed_ref, session_id=session_id)
        doc_ref = prepared["doc_ref"]
        kind = "url" if "://" in doc_path else "repo_relative_path"
        items.append(
            {
                "input": doc_path,
                "kind": kind,
                "source_kind": "repo_file",
                "resolved": {
                    "owner": doc_ref.owner,
                    "repo": doc_ref.repo,
                    "branch": doc_ref.branch,
                    "path": doc_ref.path,
                },
                "content": prepared["content_text"],
                "artifact_refs": prepared["artifact_refs"],
                "context_ref": prepared["context_ref"],
                "digest_ref": prepared["digest_ref"],
            }
        )
    return items
