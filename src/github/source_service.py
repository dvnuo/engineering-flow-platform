from __future__ import annotations

import base64
from typing import Any

from src.file_artifacts import can_project_to_text
from src.file_artifacts.service import bind_artifact_to_source_bundle, build_artifact_ref_dict, register_existing_file_as_artifact, update_projection_from_parse_result
from src.github import github_channel
from src.github.doc_refs import parse_github_doc_ref
from src.source_context import persist_github_source_bundle_and_digest
from src.utils.file_parser import parse_file, save_uploaded_file


async def prepare_github_file_source(raw: str, default_ref, *, session_id: str | None = None) -> dict:
    doc_ref = parse_github_doc_ref(raw, default_ref)
    file_data = await github_channel.get_file(doc_ref.owner, doc_ref.repo, doc_ref.path, doc_ref.branch)
    encoded = file_data.get("content")
    if not isinstance(encoded, str) or not encoded.strip():
        raise ValueError(f"File not found or empty: {doc_ref.owner}/{doc_ref.repo}/{doc_ref.path}@{doc_ref.branch}")

    content_bytes = base64.b64decode(encoded)
    file_meta = await save_uploaded_file(content_bytes, original_filename=doc_ref.path.split("/")[-1], session_id=session_id)
    artifact = register_existing_file_as_artifact(
        file_meta.file_id,
        source_type="github",
        source_kind="repo_file",
        source_locator=f"{doc_ref.owner}/{doc_ref.repo}:{doc_ref.path}@{doc_ref.branch}",
        session_id=session_id,
        provider_metadata={"sha": file_data.get("sha")},
    )

    content_markdown = ""
    source_complete = False
    partial_reasons: list[str] = []
    if can_project_to_text(file_meta.content_type, file_meta.original_filename):
        parsed = await parse_file(file_meta.file_id)
        if getattr(parsed, "success", False):
            content_markdown = parsed.markdown or ""
            update_projection_from_parse_result(artifact.artifact_id, parsed, preview=content_markdown[:2000])
            source_complete = True
        else:
            partial_reasons.append(f"parse_failed:{parsed.error}")
    else:
        partial_reasons.append("non_projectable_file")

    from src.file_artifacts.storage import storage as artifact_storage
    record = artifact_storage.get_artifact(artifact.artifact_id) or artifact
    bundle_scope_id = f"github:{doc_ref.owner}/{doc_ref.repo}:{doc_ref.path}@{doc_ref.branch}"
    bind_artifact_to_source_bundle(record.artifact_id, bundle_scope_id)
    artifact_refs = [build_artifact_ref_dict(record)]

    bundle = {
        "metadata": {
            "owner": doc_ref.owner,
            "repo": doc_ref.repo,
            "branch": doc_ref.branch,
            "path": doc_ref.path,
            "content_type": file_meta.content_type,
        },
        "content_markdown": content_markdown,
        "artifact_refs": artifact_refs,
        "completeness_ledger": {
            "file_loaded": True,
            "file_projectable": can_project_to_text(file_meta.content_type, file_meta.original_filename),
            "source_complete": source_complete,
            "partial_reasons": partial_reasons,
        },
        "raw_snapshot": {
            "path": doc_ref.path,
            "sha": file_data.get("sha"),
            "size": file_data.get("size"),
            "branch": doc_ref.branch,
        },
    }
    persisted = persist_github_source_bundle_and_digest(
        session_id=session_id or "unknown_session",
        source_id=f"{doc_ref.owner}/{doc_ref.repo}:{doc_ref.path}@{doc_ref.branch}",
        bundle=bundle,
    )
    bundle["context_ref"] = persisted["context_ref"]
    bundle["digest_ref"] = persisted["digest_ref"]
    return {"doc_ref": doc_ref, "bundle": bundle, "persisted": persisted}
