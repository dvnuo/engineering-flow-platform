"""Source-complete Jira context bundle + digest helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from src.context_blob_store import put_text
from src.utils.truncate import truncate


def _heading_block(title: str, body: str) -> str:
    text = str(body or "").strip()
    return f"## {title}\n{text if text else 'N/A'}\n"


def build_jira_source_bundle_text(bundle: Dict[str, Any]) -> str:
    metadata = bundle.get("metadata") or {}
    comments = bundle.get("comments") or []
    attachments = bundle.get("attachments") or []
    comments_lines = []
    for idx, comment in enumerate(comments, 1):
        author = ((comment.get("author") or {}).get("displayName") if isinstance(comment.get("author"), dict) else None) or "Unknown"
        created = str(comment.get("created") or "")[:19]
        body = str(comment.get("body_markdown") or comment.get("body") or "").strip()
        comments_lines.append(f"### Comment {idx}: {author} ({created})\n{body or 'N/A'}")
    attachment_lines = []
    for att in attachments:
        attachment_lines.append(
            f"- {att.get('filename','unknown')} | mime={att.get('mime_type','')} | size={att.get('size',0)} | text_preview={'yes' if att.get('text_preview') else 'no'}"
        )
    ledger = bundle.get("completeness_ledger") or {}
    sections = [
        "# Jira Source Bundle",
        _heading_block("metadata", json.dumps(metadata, ensure_ascii=False, indent=2)),
        _heading_block("description", bundle.get("description") or ""),
        _heading_block("acceptance_criteria", bundle.get("acceptance_criteria") or ""),
        _heading_block("business_rules", bundle.get("business_rules") or ""),
        _heading_block("validation_rules", bundle.get("validation_rules") or ""),
        _heading_block("comments", "\n\n".join(comments_lines)),
        _heading_block("attachments", "\n".join(attachment_lines)),
        _heading_block("artifact_refs", json.dumps(bundle.get("artifact_refs") or [], ensure_ascii=False, indent=2)),
        _heading_block("coverage_ledger", json.dumps(ledger, ensure_ascii=False, indent=2)),
        _heading_block("raw_snapshot", json.dumps(bundle.get("raw_snapshot") or {}, ensure_ascii=False, indent=2)),
    ]
    return "\n".join(sections)


def _extract_section(text: str, heading_patterns: Tuple[str, ...]) -> str:
    lines = text.splitlines()
    start = -1
    end = len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            lowered = line.lower()
            if any(p in lowered for p in heading_patterns):
                start = i + 1
                continue
            if start >= 0:
                end = i
                break
    if start < 0:
        return ""
    return "\n".join(lines[start:end]).strip()


def build_jira_source_digest(bundle: Dict[str, Any], *, max_chars: int = 7000) -> Dict[str, Any]:
    metadata = bundle.get("metadata") or {}
    comments = bundle.get("comments") or []
    attachments = bundle.get("attachments") or []
    ledger = bundle.get("completeness_ledger") or {}

    comment_indexes = [str(i) for i in range(1, len(comments) + 1)]
    comment_index_text = ",".join(comment_indexes)
    if len(comment_index_text) > 800:
        comment_index_text = f"1..{len(comments)}"

    comment_summary = []
    for idx, comment in enumerate(comments, 1):
        author = ((comment.get("author") or {}).get("displayName") if isinstance(comment.get("author"), dict) else None) or "Unknown"
        created = str(comment.get("created") or "")[:10]
        body = str(comment.get("body_markdown") or comment.get("body") or "")
        body_one_line = body.replace("\n", " ")
        comment_summary.append(f"- [{idx}] {author} {created}: {truncate(body_one_line, 180)}")

    coverage = {
        "metadata": "covered" if metadata else "missing",
        "description": "covered" if bundle.get("description") else "missing",
        "acceptance_criteria": "covered" if bundle.get("acceptance_criteria") else "missing",
        "business_rules": "covered" if bundle.get("business_rules") else "missing",
        "validation_rules": "covered" if bundle.get("validation_rules") else "missing",
        "comments": f"{ledger.get('comments_loaded', len(comments))}/{ledger.get('comments_total', len(comments))} covered",
        "comment_indexes": comment_index_text,
        "attachments_metadata": f"{ledger.get('attachments_metadata_loaded', len(attachments))}/{ledger.get('attachments_total', len(attachments))} covered",
        "text_attachments": f"{ledger.get('text_attachments_loaded', 0)}/{ledger.get('text_attachments_total', 0)} covered",
        "artifact_refs": len(bundle.get("artifact_refs") or []),
    }

    digest_text = "\n".join(
        [
            "[source digest]",
            f"issue_key: {metadata.get('key', '')}",
            f"source_complete: {bool(ledger.get('source_complete', False))}",
            f"title: {metadata.get('title', '')}",
            f"status: {metadata.get('status', '')}",
            "\n[description]\n" + truncate(str(bundle.get("description") or "N/A"), 1200),
            "\n[acceptance criteria]\n" + truncate(str(bundle.get("acceptance_criteria") or "N/A"), 1200),
            "\n[business rules]\n" + truncate(str(bundle.get("business_rules") or "N/A"), 800),
            "\n[validation rules]\n" + truncate(str(bundle.get("validation_rules") or "N/A"), 800),
            "\n[comments summarized]\n" + "\n".join(comment_summary),
            "\n[coverage]\n" + json.dumps(coverage, ensure_ascii=False, indent=2),
            "\n[partial reasons]\n" + json.dumps(ledger.get("partial_reasons") or [], ensure_ascii=False),
        ]
    )
    return {
        "digest_text": truncate(digest_text, max_chars),
        "coverage": coverage,
        "source_complete": bool(ledger.get("source_complete", False)),
        "partial_reasons": list(ledger.get("partial_reasons") or []),
    }


def persist_jira_source_bundle_and_digest(
    *,
    session_id: str,
    issue_key: str,
    bundle: Dict[str, Any],
) -> Dict[str, Any]:
    bundle_text = build_jira_source_bundle_text(bundle)
    context_ref = put_text(
        session_id=session_id,
        kind="jira_source_bundle",
        source_id=issue_key,
        title=f"Jira source bundle {issue_key}",
        content=bundle_text,
        metadata={"issue_key": issue_key, "source_complete": bundle.get("completeness_ledger", {}).get("source_complete")},
    )
    digest = build_jira_source_digest(bundle)
    chunk_refs: List[str] = []
    comments = bundle.get("comments") or []
    attachments = bundle.get("attachments") or []
    chunk_payloads = [
        ("metadata_description_ac", json.dumps({
            "metadata": bundle.get("metadata") or {},
            "description": bundle.get("description") or "",
            "acceptance_criteria": bundle.get("acceptance_criteria") or "",
        }, ensure_ascii=False, indent=2)),
        ("attachments", json.dumps({"attachments": attachments}, ensure_ascii=False, indent=2)),
        ("raw_field_index", json.dumps({"raw_snapshot_keys": list((bundle.get("raw_snapshot") or {}).keys())}, ensure_ascii=False, indent=2)),
    ]
    if comments:
        for i in range(0, len(comments), 25):
            idx = i // 25 + 1
            chunk_payloads.append((f"comments_chunk_{idx}", json.dumps({"comments": comments[i:i + 25]}, ensure_ascii=False, indent=2)))
    for chunk_kind, chunk_body in chunk_payloads:
        if len(chunk_body) <= 12000:
            chunk_ref = put_text(
                session_id=session_id,
                kind="jira_source_digest_chunk",
                source_id=f"{issue_key}_{chunk_kind}",
                title=f"Jira digest chunk {chunk_kind}",
                content=chunk_body,
                metadata={"issue_key": issue_key, "chunk_kind": chunk_kind},
            )
            chunk_refs.append(chunk_ref)
            continue
        for part_idx, start in enumerate(range(0, len(chunk_body), 10000), 1):
            part_body = chunk_body[start:start + 10000]
            chunk_ref = put_text(
                session_id=session_id,
                kind="jira_source_digest_chunk",
                source_id=f"{issue_key}_{chunk_kind}_part_{part_idx}",
                title=f"Jira digest chunk {chunk_kind} part {part_idx}",
                content=part_body,
                metadata={"issue_key": issue_key, "chunk_kind": chunk_kind, "part_index": part_idx},
            )
            chunk_refs.append(chunk_ref)
    digest_overview = (
        digest["digest_text"]
        + "\n\n[source digest chunks]\n"
        + f"chunk_count: {len(chunk_refs)}\n"
        + "\n".join(f"- {ref}" for ref in chunk_refs)
    )
    digest_ref = put_text(
        session_id=session_id,
        kind="jira_source_digest",
        source_id=issue_key,
        title=f"Jira source digest {issue_key}",
        content=digest_overview,
        metadata={"issue_key": issue_key, "source_ref": context_ref, "source_complete": digest["source_complete"]},
    )
    return {
        "context_ref": context_ref,
        "digest_ref": digest_ref,
        "digest_text": digest["digest_text"],
        "coverage": digest["coverage"],
        "source_complete": digest["source_complete"],
        "partial_reasons": digest["partial_reasons"],
        "source_digest_chunk_refs": chunk_refs,
        "source_digest_chunk_count": len(chunk_refs),
    }


def extract_markdown_section(markdown_text: str, heading: str) -> str:
    pattern = re.escape(heading.lower())
    return _extract_section(markdown_text or "", (pattern,))


def persist_confluence_source_bundle_and_digest(
    *,
    session_id: str,
    page_id: str,
    bundle: Dict[str, Any],
) -> Dict[str, Any]:
    bundle_text = "\n".join(
        [
            "# Confluence Source Bundle",
            _heading_block("metadata", json.dumps(bundle.get("metadata") or {}, ensure_ascii=False, indent=2)),
            _heading_block("content", str(bundle.get("content_markdown") or "")),
            _heading_block("comments", json.dumps(bundle.get("comments") or [], ensure_ascii=False, indent=2)),
            _heading_block("attachments", json.dumps(bundle.get("attachments") or [], ensure_ascii=False, indent=2)),
            _heading_block("artifact_refs", json.dumps(bundle.get("artifact_refs") or [], ensure_ascii=False, indent=2)),
            _heading_block("children", json.dumps(bundle.get("children") or [], ensure_ascii=False, indent=2)),
            _heading_block("descendants", json.dumps(bundle.get("descendants") or [], ensure_ascii=False, indent=2)),
            _heading_block("coverage_ledger", json.dumps(bundle.get("completeness_ledger") or {}, ensure_ascii=False, indent=2)),
            _heading_block("raw_snapshot", json.dumps(bundle.get("raw_snapshot") or {}, ensure_ascii=False, indent=2)),
        ]
    )
    context_ref = put_text(
        session_id=session_id,
        kind="confluence_source_bundle",
        source_id=page_id,
        title=f"Confluence source bundle {page_id}",
        content=bundle_text,
        metadata={"page_id": page_id, "source_complete": bundle.get("completeness_ledger", {}).get("source_complete")},
    )
    digest_text = truncate(
        json.dumps(
            {
                "source_ref": context_ref,
                "metadata": bundle.get("metadata") or {},
                "coverage": bundle.get("completeness_ledger") or {},
                "open_questions": bundle.get("open_questions") or [],
                "artifact_refs": len(bundle.get("artifact_refs") or []),
            },
            ensure_ascii=False,
            indent=2,
        ),
        7000,
    )
    digest_ref = put_text(
        session_id=session_id,
        kind="confluence_source_digest",
        source_id=page_id,
        title=f"Confluence source digest {page_id}",
        content=digest_text,
        metadata={"page_id": page_id, "source_ref": context_ref},
    )
    return {
        "context_ref": context_ref,
        "digest_ref": digest_ref,
        "source_complete": bool((bundle.get("completeness_ledger") or {}).get("source_complete")),
    }



def build_github_source_bundle_text(bundle: Dict[str, Any]) -> str:
    return "\n".join([
        "# GitHub Source Bundle",
        _heading_block("metadata", json.dumps(bundle.get("metadata") or {}, ensure_ascii=False, indent=2)),
        _heading_block("content", str(bundle.get("content_markdown") or "")),
        _heading_block("artifact_refs", json.dumps(bundle.get("artifact_refs") or [], ensure_ascii=False, indent=2)),
        _heading_block("coverage_ledger", json.dumps(bundle.get("completeness_ledger") or {}, ensure_ascii=False, indent=2)),
        _heading_block("raw_snapshot", json.dumps(bundle.get("raw_snapshot") or {}, ensure_ascii=False, indent=2)),
    ])


def persist_github_source_bundle_and_digest(*, session_id: str, source_id: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
    bundle_text = build_github_source_bundle_text(bundle)
    context_ref = put_text(
        session_id=session_id,
        kind="github_source_bundle",
        source_id=source_id,
        title=f"GitHub source bundle {source_id}",
        content=bundle_text,
        metadata={"source_id": source_id, "source_complete": bundle.get("completeness_ledger", {}).get("source_complete")},
    )
    digest_text = truncate(json.dumps({
        "source_ref": context_ref,
        "metadata": bundle.get("metadata") or {},
        "coverage": bundle.get("completeness_ledger") or {},
        "artifact_refs": len(bundle.get("artifact_refs") or []),
        "source_kind": (bundle.get("metadata") or {}).get("source_kind", "repo_file"),
        "attachments_supported": bool((bundle.get("metadata") or {}).get("attachments_supported", False)),
        "issue_pr_assets_supported": bool((bundle.get("metadata") or {}).get("issue_pr_assets_supported", False)),
    }, ensure_ascii=False, indent=2), 7000)
    digest_ref = put_text(
        session_id=session_id,
        kind="github_source_digest",
        source_id=source_id,
        title=f"GitHub source digest {source_id}",
        content=digest_text,
        metadata={"source_id": source_id, "source_ref": context_ref},
    )
    return {"context_ref": context_ref, "digest_ref": digest_ref, "source_complete": bool((bundle.get("completeness_ledger") or {}).get("source_complete"))}
