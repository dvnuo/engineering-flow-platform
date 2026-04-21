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
    digest_ref = put_text(
        session_id=session_id,
        kind="jira_source_digest",
        source_id=issue_key,
        title=f"Jira source digest {issue_key}",
        content=digest["digest_text"],
        metadata={"issue_key": issue_key, "source_ref": context_ref, "source_complete": digest["source_complete"]},
    )
    return {
        "context_ref": context_ref,
        "digest_ref": digest_ref,
        "digest_text": digest["digest_text"],
        "coverage": digest["coverage"],
        "source_complete": digest["source_complete"],
        "partial_reasons": digest["partial_reasons"],
    }


def extract_markdown_section(markdown_text: str, heading: str) -> str:
    pattern = re.escape(heading.lower())
    return _extract_section(markdown_text or "", (pattern,))
