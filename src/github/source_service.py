from __future__ import annotations

import base64
import mimetypes
from typing import Any

from src.file_artifacts import can_project_to_text
from src.file_artifacts.service import (
    attach_source_refs_to_artifact,
    bind_artifact_to_source_bundle,
    build_artifact_ref_dict,
    register_existing_file_as_artifact,
    update_projection_from_parse_result,
)
from src.file_artifacts.storage import storage as artifact_storage
from src.github.api import github_channel
from src.github.asset_links import extract_github_asset_urls
from src.github.doc_refs import parse_github_doc_ref
from src.source_context import persist_github_source_bundle_and_digest
from src.utils.attachment import download_and_process_attachment
from src.utils.file_parser import parse_file, save_uploaded_file


def _asset_source_id(repo_full_name: str, parent_id: str, segment: str, index: int) -> str:
    return f"{repo_full_name}#{parent_id}:{segment}:{index}"


async def _materialize_asset(
    *,
    url: str,
    session_id: str | None,
    source_kind: str,
    source_locator: str,
    persist_kind: str,
    persist_source_id: str,
    persist_title: str,
    metadata: dict,
) -> dict:
    result = await download_and_process_attachment(
        url=url,
        session_id=session_id,
        source_type="github",
        source_kind=source_kind,
        source_locator=source_locator,
        provider_metadata=metadata,
        persist_text_ref_session_id=session_id,
        persist_text_ref_kind=persist_kind,
        persist_text_ref_source_id=persist_source_id,
        persist_text_ref_title=persist_title,
        persist_text_ref_metadata=metadata,
    )
    return {
        "url": url,
        "source_kind": source_kind,
        "source_locator": source_locator,
        "artifact_id": result.artifact_id,
        "text_ref": result.text_ref,
        "parse_status": result.parse_status,
        "parse_error": result.parse_error,
        "projected_to_text": bool(getattr(result, "projected_to_text", False)),
        "content_type": result.content_type,
        "filename": result.filename,
        "artifact_ref": build_artifact_ref_dict(artifact_storage.get_artifact(result.artifact_id)) if result.artifact_id and artifact_storage.get_artifact(result.artifact_id) else None,
    }


def _build_asset_ledger(*, source_kind: str, body_loaded: bool, comments_loaded: bool, review_comments_loaded: bool, asset_entries: list[dict], partial_reasons: list[str]) -> dict:
    projectable_assets_total = sum(1 for e in asset_entries if can_project_to_text(e.get("content_type"), e.get("filename")))
    text_assets_loaded = sum(1 for e in asset_entries if e.get("parse_status") == "completed" and e.get("projected_to_text"))
    text_assets_with_full_ref = sum(1 for e in asset_entries if e.get("text_ref"))

    source_complete_for_generation = body_loaded and comments_loaded and (review_comments_loaded if source_kind == "pull_request" else True)
    if projectable_assets_total > 0:
        source_complete_for_generation = source_complete_for_generation and (text_assets_loaded >= projectable_assets_total)

    source_complete_including_binary_bodies = source_complete_for_generation and (len(asset_entries) == 0 or len(asset_entries) >= projectable_assets_total)
    source_complete = source_complete_for_generation

    return {
        "source_kind": source_kind,
        "body_loaded": body_loaded,
        "comments_loaded": comments_loaded,
        "review_comments_loaded": review_comments_loaded,
        "asset_urls_total": len(asset_entries),
        "asset_entries_created": len(asset_entries),
        "projectable_assets_total": projectable_assets_total,
        "text_assets_loaded": text_assets_loaded,
        "text_assets_with_full_ref": text_assets_with_full_ref,
        "partial_reasons": partial_reasons,
        "source_complete": source_complete,
        "source_complete_for_generation": source_complete_for_generation,
        "source_complete_including_binary_bodies": source_complete_including_binary_bodies,
    }


def _finalize_bundle_artifacts(
    *,
    asset_entries: list[dict],
    bundle_scope_id: str,
    context_ref: str | None,
    digest_ref: str | None,
) -> tuple[list[dict], list[dict]]:
    refreshed_asset_entries: list[dict] = []
    refreshed_bundle_artifact_refs: list[dict] = []
    seen_artifact_ids: set[str] = set()

    for entry in asset_entries:
        refreshed_entry = dict(entry or {})
        artifact_id = str(refreshed_entry.get("artifact_id") or "").strip()
        if not artifact_id:
            refreshed_asset_entries.append(refreshed_entry)
            continue

        bind_artifact_to_source_bundle(artifact_id, bundle_scope_id)
        if context_ref and digest_ref:
            attach_source_refs_to_artifact(
                artifact_id,
                context_ref=context_ref,
                digest_ref=digest_ref,
            )

        record = artifact_storage.get_artifact(artifact_id)
        if record:
            refreshed_ref = build_artifact_ref_dict(record)
            refreshed_entry["artifact_ref"] = refreshed_ref
            if not refreshed_entry.get("text_ref") and record.text_ref:
                refreshed_entry["text_ref"] = record.text_ref
            if artifact_id not in seen_artifact_ids:
                refreshed_bundle_artifact_refs.append(refreshed_ref)
                seen_artifact_ids.add(artifact_id)

        refreshed_asset_entries.append(refreshed_entry)

    return refreshed_asset_entries, refreshed_bundle_artifact_refs


async def prepare_github_file_source(raw: str, default_ref, *, session_id: str | None = None) -> dict:
    doc_ref = parse_github_doc_ref(raw, default_ref)
    file_data = await github_channel.get_file(doc_ref.owner, doc_ref.repo, doc_ref.path, doc_ref.branch)
    encoded = file_data.get("content")
    if not isinstance(encoded, str) or not encoded.strip():
        raise ValueError(f"File not found or empty: {doc_ref.owner}/{doc_ref.repo}/{doc_ref.path}@{doc_ref.branch}")

    content_bytes = base64.b64decode(encoded)
    guessed_content_type = mimetypes.guess_type(doc_ref.path)[0]
    if not guessed_content_type:
        try:
            content_bytes.decode("utf-8")
            guessed_content_type = "text/plain"
        except Exception:
            guessed_content_type = None
    file_meta = await save_uploaded_file(
        content_bytes,
        original_filename=doc_ref.path.split("/")[-1],
        session_id=session_id,
        content_type=guessed_content_type,
    )
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
        try:
            parsed = await parse_file(file_meta.file_id)
            if getattr(parsed, "success", False):
                content_markdown = parsed.markdown or ""
                update_projection_from_parse_result(
                    artifact.artifact_id,
                    parsed,
                    preview=content_markdown[:2000],
                    persist_text_ref_session_id=session_id,
                    persist_text_ref_kind="github_file_text",
                    persist_text_ref_source_id=f"{doc_ref.owner}/{doc_ref.repo}:{doc_ref.path}@{doc_ref.branch}",
                    persist_text_ref_title=f"GitHub file {doc_ref.path}",
                    persist_text_ref_metadata={
                        "owner": doc_ref.owner,
                        "repo": doc_ref.repo,
                        "branch": doc_ref.branch,
                        "path": doc_ref.path,
                        "sha": file_data.get("sha"),
                    },
                )
                source_complete = True
            else:
                parse_error = str(getattr(parsed, "error", "parse failed"))
                artifact_storage.update_artifact_status(
                    artifact.artifact_id,
                    parse_status="failed",
                    parse_error=parse_error,
                )
                partial_reasons.append(f"parse_failed:{parse_error}")
        except Exception as exc:
            artifact_storage.update_artifact_status(
                artifact.artifact_id,
                parse_status="failed",
                parse_error=str(exc),
            )
            partial_reasons.append(f"parse_failed:{type(exc).__name__}")
    else:
        artifact_storage.update_artifact_status(artifact.artifact_id, parse_status="skipped")
        partial_reasons.append("non_projectable_file")

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
            "source_kind": "repo_file",
            "attachments_supported": False,
            "issue_pr_assets_supported": False,
        },
        "content_markdown": content_markdown,
        "artifact_refs": artifact_refs,
        "completeness_ledger": {
            "file_loaded": True,
            "file_projectable": can_project_to_text(file_meta.content_type, file_meta.original_filename),
            "source_complete": source_complete,
            "source_kind": "repo_file",
            "attachments_supported": False,
            "issue_pr_assets_supported": False,
            "partial_reasons": partial_reasons,
        },
        "raw_snapshot": {
            "path": doc_ref.path,
            "sha": file_data.get("sha"),
            "size": file_data.get("size"),
            "branch": doc_ref.branch,
        },
    }
    persisted: dict[str, Any] = {}
    if session_id:
        persisted = persist_github_source_bundle_and_digest(
            session_id=session_id,
            source_id=f"{doc_ref.owner}/{doc_ref.repo}:{doc_ref.path}@{doc_ref.branch}",
            bundle=bundle,
        )
        bundle["context_ref"] = persisted["context_ref"]
        bundle["digest_ref"] = persisted["digest_ref"]
        attach_source_refs_to_artifact(
            artifact.artifact_id,
            context_ref=bundle["context_ref"],
            digest_ref=bundle["digest_ref"],
        )
    else:
        bundle["context_ref"] = None
        bundle["digest_ref"] = None
        if "session_scope_missing" not in partial_reasons:
            partial_reasons.append("session_scope_missing")
    refreshed = artifact_storage.get_artifact(artifact.artifact_id)
    if refreshed:
        bundle["artifact_refs"] = [build_artifact_ref_dict(refreshed)]
    return {"doc_ref": doc_ref, "bundle": bundle, "persisted": persisted}


async def prepare_github_issue_source(
    owner: str,
    repo: str,
    issue_number: int,
    *,
    session_id: str | None = None,
    include_comments: bool = True,
    include_assets: bool = True,
) -> dict:
    issue = await github_channel.get_issue(owner, repo, issue_number)
    comments = await github_channel.get_issue_comments(owner, repo, issue_number) if include_comments else []
    repo_full_name = f"{owner}/{repo}"

    asset_entries: list[dict] = []
    partial_reasons: list[str] = []
    if include_assets:
        body_urls = extract_github_asset_urls(str(issue.get("body") or ""))
        for idx, url in enumerate(body_urls, start=1):
            locator = _asset_source_id(repo_full_name, str(issue_number), "issue_body", idx)
            entry = await _materialize_asset(
                url=url,
                session_id=session_id,
                source_kind="issue_asset",
                source_locator=locator,
                persist_kind="github_issue_asset_text",
                persist_source_id=locator,
                persist_title=f"GitHub issue asset {repo_full_name}#{issue_number}",
                metadata={"owner": owner, "repo": repo, "issue_number": issue_number, "segment": "issue_body", "asset_index": idx},
            )
            asset_entries.append(entry)
            if entry.get("parse_status") == "failed":
                partial_reasons.append(f"asset_parse_failed:{locator}")

        for comment in comments:
            cid = comment.get("id") or "unknown"
            comment_urls = extract_github_asset_urls(str(comment.get("body") or ""))
            for idx, url in enumerate(comment_urls, start=1):
                locator = _asset_source_id(repo_full_name, str(issue_number), f"comment:{cid}", idx)
                entry = await _materialize_asset(
                    url=url,
                    session_id=session_id,
                    source_kind="issue_comment_asset",
                    source_locator=locator,
                    persist_kind="github_issue_asset_text",
                    persist_source_id=locator,
                    persist_title=f"GitHub issue comment asset {repo_full_name}#{issue_number}",
                    metadata={"owner": owner, "repo": repo, "issue_number": issue_number, "comment_id": cid, "segment": "comment", "asset_index": idx},
                )
                asset_entries.append(entry)
                if entry.get("parse_status") == "failed":
                    partial_reasons.append(f"asset_parse_failed:{locator}")

    artifact_refs = [e["artifact_ref"] for e in asset_entries if e.get("artifact_ref")]
    body_loaded = bool(str(issue.get("body") or "").strip())
    comments_loaded = (not include_comments) or isinstance(comments, list)
    ledger = _build_asset_ledger(
        source_kind="issue",
        body_loaded=body_loaded,
        comments_loaded=comments_loaded,
        review_comments_loaded=True,
        asset_entries=asset_entries,
        partial_reasons=partial_reasons,
    )

    bundle = {
        "metadata": {
            "source_kind": "issue",
            "owner": owner,
            "repo": repo,
            "repo_full_name": repo_full_name,
            "issue_number": issue_number,
            "attachments_supported": True,
            "issue_pr_assets_supported": True,
            "title": issue.get("title"),
            "state": issue.get("state"),
        },
        "body_markdown": str(issue.get("body") or ""),
        "comments": comments,
        "asset_entries": asset_entries,
        "artifact_refs": artifact_refs,
        "completeness_ledger": ledger,
        "raw_snapshot": {"issue": issue, "comments": comments},
    }

    persisted: dict[str, Any] = {}
    if session_id:
        persisted = persist_github_source_bundle_and_digest(
            session_id=session_id,
            source_id=f"{repo_full_name}#issue:{issue_number}",
            bundle=bundle,
        )
        bundle["context_ref"] = persisted["context_ref"]
        bundle["digest_ref"] = persisted["digest_ref"]
    else:
        bundle["context_ref"] = None
        bundle["digest_ref"] = None
        ledger.setdefault("partial_reasons", []).append("session_scope_missing")

    refreshed_asset_entries, refreshed_artifact_refs = _finalize_bundle_artifacts(
        asset_entries=asset_entries,
        bundle_scope_id=f"github:{repo_full_name}#issue:{issue_number}",
        context_ref=bundle.get("context_ref"),
        digest_ref=bundle.get("digest_ref"),
    )
    bundle["asset_entries"] = refreshed_asset_entries
    bundle["artifact_refs"] = refreshed_artifact_refs

    return {"bundle": bundle, "persisted": persisted}


async def prepare_github_pr_source(
    owner: str,
    repo: str,
    pull_number: int,
    *,
    session_id: str | None = None,
    include_issue_comments: bool = True,
    include_review_comments: bool = True,
    include_assets: bool = True,
) -> dict:
    pr = await github_channel.get_pull_request(owner, repo, pull_number)
    issue_comments = await github_channel.get_issue_comments(owner, repo, pull_number) if include_issue_comments else []
    review_comments = await github_channel.get_pr_comments(owner, repo, pull_number) if include_review_comments else []
    repo_full_name = f"{owner}/{repo}"

    asset_entries: list[dict] = []
    partial_reasons: list[str] = []
    if include_assets:
        body_urls = extract_github_asset_urls(str(pr.get("body") or ""))
        for idx, url in enumerate(body_urls, start=1):
            locator = _asset_source_id(repo_full_name, str(pull_number), "pr_body", idx)
            entry = await _materialize_asset(
                url=url,
                session_id=session_id,
                source_kind="pr_asset",
                source_locator=locator,
                persist_kind="github_pr_asset_text",
                persist_source_id=locator,
                persist_title=f"GitHub PR asset {repo_full_name}#{pull_number}",
                metadata={"owner": owner, "repo": repo, "pull_number": pull_number, "segment": "pr_body", "asset_index": idx},
            )
            asset_entries.append(entry)
            if entry.get("parse_status") == "failed":
                partial_reasons.append(f"asset_parse_failed:{locator}")

        for comment in issue_comments:
            cid = comment.get("id") or "unknown"
            for idx, url in enumerate(extract_github_asset_urls(str(comment.get("body") or "")), start=1):
                locator = _asset_source_id(repo_full_name, str(pull_number), f"pr_comment:{cid}", idx)
                entry = await _materialize_asset(
                    url=url,
                    session_id=session_id,
                    source_kind="pr_comment_asset",
                    source_locator=locator,
                    persist_kind="github_pr_asset_text",
                    persist_source_id=locator,
                    persist_title=f"GitHub PR comment asset {repo_full_name}#{pull_number}",
                    metadata={"owner": owner, "repo": repo, "pull_number": pull_number, "comment_id": cid, "segment": "pr_comment", "asset_index": idx},
                )
                asset_entries.append(entry)
                if entry.get("parse_status") == "failed":
                    partial_reasons.append(f"asset_parse_failed:{locator}")

        review_list = review_comments if isinstance(review_comments, list) else []
        for comment in review_list:
            cid = comment.get("id") or "unknown"
            for idx, url in enumerate(extract_github_asset_urls(str(comment.get("body") or "")), start=1):
                locator = _asset_source_id(repo_full_name, str(pull_number), f"pr_review_comment:{cid}", idx)
                entry = await _materialize_asset(
                    url=url,
                    session_id=session_id,
                    source_kind="pr_review_comment_asset",
                    source_locator=locator,
                    persist_kind="github_pr_asset_text",
                    persist_source_id=locator,
                    persist_title=f"GitHub PR review comment asset {repo_full_name}#{pull_number}",
                    metadata={"owner": owner, "repo": repo, "pull_number": pull_number, "comment_id": cid, "segment": "pr_review_comment", "asset_index": idx},
                )
                asset_entries.append(entry)
                if entry.get("parse_status") == "failed":
                    partial_reasons.append(f"asset_parse_failed:{locator}")

    artifact_refs = [e["artifact_ref"] for e in asset_entries if e.get("artifact_ref")]
    ledger = _build_asset_ledger(
        source_kind="pull_request",
        body_loaded=bool(str(pr.get("body") or "").strip()),
        comments_loaded=(not include_issue_comments) or isinstance(issue_comments, list),
        review_comments_loaded=(not include_review_comments) or isinstance(review_comments, list),
        asset_entries=asset_entries,
        partial_reasons=partial_reasons,
    )

    bundle = {
        "metadata": {
            "source_kind": "pull_request",
            "owner": owner,
            "repo": repo,
            "repo_full_name": repo_full_name,
            "pull_number": pull_number,
            "attachments_supported": True,
            "issue_pr_assets_supported": True,
            "title": pr.get("title"),
            "state": pr.get("state"),
        },
        "body_markdown": str(pr.get("body") or ""),
        "issue_comments": issue_comments,
        "review_comments": review_comments,
        "asset_entries": asset_entries,
        "artifact_refs": artifact_refs,
        "completeness_ledger": ledger,
        "raw_snapshot": {"pull_request": pr, "issue_comments": issue_comments, "review_comments": review_comments},
    }

    persisted: dict[str, Any] = {}
    if session_id:
        persisted = persist_github_source_bundle_and_digest(
            session_id=session_id,
            source_id=f"{repo_full_name}#pull_request:{pull_number}",
            bundle=bundle,
        )
        bundle["context_ref"] = persisted["context_ref"]
        bundle["digest_ref"] = persisted["digest_ref"]
    else:
        bundle["context_ref"] = None
        bundle["digest_ref"] = None
        ledger.setdefault("partial_reasons", []).append("session_scope_missing")

    refreshed_asset_entries, refreshed_artifact_refs = _finalize_bundle_artifacts(
        asset_entries=asset_entries,
        bundle_scope_id=f"github:{repo_full_name}#pull_request:{pull_number}",
        context_ref=bundle.get("context_ref"),
        digest_ref=bundle.get("digest_ref"),
    )
    bundle["asset_entries"] = refreshed_asset_entries
    bundle["artifact_refs"] = refreshed_artifact_refs

    return {"bundle": bundle, "persisted": persisted}
