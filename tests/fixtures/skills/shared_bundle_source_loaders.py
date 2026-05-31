from src.runtime.requirement_bundle_assets import parse_bundle_ref, prepare_github_doc_source


async def _load_jira_sources(jira_ids, session_id=None):
    raise RuntimeError("Jira source loading is no longer provided by Python integration modules; use the external jira CLI adapter.")


async def _load_confluence_sources(confluence_ids, session_id=None):
    raise RuntimeError("Confluence source loading is no longer provided by Python integration modules; use the external confluence CLI adapter.")


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
