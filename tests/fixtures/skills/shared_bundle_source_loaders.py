from src.jira.source_service import format_jira_source_manifest, prepare_jira_issue_source
from src.confluence.source_service import format_confluence_source_manifest, prepare_confluence_page_source
from src.runtime.requirement_bundle_assets import parse_bundle_ref, prepare_github_doc_source


async def _load_jira_sources(jira_ids, session_id=None):
    items = []
    for source in jira_ids:
        prepared = await prepare_jira_issue_source(source, session_id=session_id)
        items.append({
            "content": format_jira_source_manifest(prepared),
            "source_kind": "jira_issue",
            "artifact_refs": prepared.bundle.get("artifact_refs", []),
            "context_ref": prepared.manifest.get("context_ref"),
            "digest_ref": prepared.manifest.get("digest_ref"),
        })
    return items


async def _load_confluence_sources(confluence_ids, session_id=None):
    items = []
    for source in confluence_ids:
        prepared = await prepare_confluence_page_source(source, session_id=session_id)
        manifest = prepared.get("manifest", {})
        items.append({
            "content": format_confluence_source_manifest(prepared),
            "source_kind": "confluence_page",
            "artifact_refs": prepared.get("artifact_refs", []),
            "context_ref": manifest.get("context_ref"),
            "digest_ref": manifest.get("digest_ref"),
        })
    return items


async def _load_github_doc_sources(bundle_ref, doc_sources, session_id=None):
    bundle_ref_obj = parse_bundle_ref(bundle_ref)
    default_ref = {
        "owner": bundle_ref_obj.owner,
        "repo": bundle_ref_obj.repo,
        "branch": bundle_ref_obj.branch,
        "path": bundle_ref_obj.path,
    }
    items = []
    for raw in doc_sources:
        prepared = await prepare_github_doc_source(raw, default_ref, session_id=session_id)
        items.append({
            "content": prepared.get("content_text", ""),
            "source_kind": "repo_file",
            "artifact_refs": prepared.get("artifact_refs", []),
            "context_ref": prepared.get("context_ref"),
            "digest_ref": prepared.get("digest_ref"),
        })
    return items
