from __future__ import annotations

import json
from typing import Optional

from .source_service import JiraIssueSourceResult


def _comment_author_name(author) -> str:
    if isinstance(author, dict):
        return author.get("displayName") or author.get("name") or "Unknown"
    if isinstance(author, str) and author.strip():
        return author.strip()
    return "Unknown"


def render_jira_issue_export_markdown(
    source: JiraIssueSourceResult,
    *,
    downloaded_attachments: Optional[list[dict]] = None,
    include_raw_snapshot: bool = False,
    include_coverage_ledger: bool = True,
    max_comments: Optional[int] = 10,
    comments_order: str = "latest_first",
) -> str:
    bundle = source.bundle or {}
    metadata = bundle.get("metadata", {}) or {}
    issue_key = source.issue_key
    title = metadata.get("title") or source.fields.get("summary") or ""

    description = bundle.get("description") or ""
    description = source.adapter._strip_acceptance_criteria_from_markdown_description(description) if description else ""
    acceptance = bundle.get("acceptance_criteria") or "N/A"

    comments = list(bundle.get("comments") or [])
    comments.sort(key=lambda c: str(c.get("created") or ""))
    if comments_order == "latest_first":
        comments.reverse()
    if max_comments is not None and int(max_comments) > 0:
        comments = comments[: int(max_comments)]

    lines: list[str] = []
    lines.append(f"# {issue_key}: {title}")
    lines.append("")
    lines.append("## Metadata")
    lines.append(f"- Key: {metadata.get('key') or issue_key}")
    lines.append(f"- Status: {metadata.get('status') or 'N/A'}")
    lines.append(f"- Type: {metadata.get('type') or 'N/A'}")
    lines.append(f"- Priority: {metadata.get('priority') or 'N/A'}")
    lines.append(f"- Assignee: {metadata.get('assignee') or 'N/A'}")

    lines.append("")
    lines.append("## Description")
    lines.append(description or "N/A")

    lines.append("")
    lines.append("## Acceptance Criteria")
    lines.append(acceptance)

    lines.append("")
    lines.append("## Comments")
    if comments:
        for idx, c in enumerate(comments, 1):
            author = _comment_author_name(c.get("author"))
            created = c.get("created") or "N/A"
            body = c.get("body_markdown") or source.adapter._convert_description_to_markdown(c.get("body")) or "N/A"
            lines.append(f"### {idx}) {author} - {created}")
            lines.append(body)
            lines.append("")
    else:
        lines.append("N/A")

    lines.append("## Attachments")
    attachments_to_render = downloaded_attachments if downloaded_attachments is not None else (bundle.get("attachments") or [])
    if attachments_to_render:
        for item in attachments_to_render:
            filename = item.get("filename", "unknown")
            status = item.get("status")
            if status == "saved":
                rel_path = item.get("path") or filename
                size = item.get("size")
                lines.append(f"- [{filename}]({rel_path}) — saved, {size} bytes")
            elif status in {"skipped", "failed"}:
                lines.append(f"- {filename} — {status}: {item.get('reason', 'N/A')}")
            else:
                lines.append(f"- {filename}")
    else:
        lines.append("N/A")

    if include_coverage_ledger:
        lines.append("")
        lines.append("## Source Coverage")
        lines.append("```json")
        lines.append(json.dumps(bundle.get("completeness_ledger") or {}, ensure_ascii=False, indent=2, default=str))
        lines.append("```")

    if include_raw_snapshot:
        lines.append("")
        lines.append("## Raw Fields Snapshot")
        lines.append("```json")
        lines.append(json.dumps(bundle.get("raw_snapshot") or {}, ensure_ascii=False, indent=2, default=str))
        lines.append("```")

    return "\n".join(lines).strip() + "\n"
