from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.source_context import persist_jira_source_bundle_and_digest
from src.source_bundle_completeness import apply_session_scope_requirement
from src.file_artifacts import can_project_to_text
from src.file_artifacts.service import attach_source_refs_to_artifact, bind_artifact_to_source_bundle, build_artifact_ref_dict
from src.file_artifacts.storage import storage as artifact_storage
from src.utils.attachment import download_and_process_attachment as _default_download_and_process_attachment

from .adapter import JiraFormatAdapter


@dataclass
class JiraIssueSourceResult:
    issue_key: str
    issue: dict
    fields: dict
    bundle: dict
    manifest: dict
    persisted: dict
    channel: Any
    adapter: JiraFormatAdapter
    attachment_list: list[dict]


async def prepare_jira_issue_source(
    issue_key_or_url: str,
    *,
    include_all_comments: bool = True,
    include_attachments: bool = True,
    include_raw_snapshot: bool = True,
    session_id: str | None = None,
    attachment_body_policy: str = "source_complete",
) -> JiraIssueSourceResult:
    from src import jira as jira_module

    channel = getattr(jira_module, "jira_channel", None)
    downloader = getattr(jira_module, "download_and_process_attachment", _default_download_and_process_attachment)

    if not channel or not channel.is_configured():
        raise RuntimeError("Jira is not configured.")

    issue_key = str(issue_key_or_url or "").strip()
    instance_channel = channel
    if "/browse/" in issue_key:
        match = re.search(r"/browse/([A-Z][A-Z0-9_]*-\d+)", issue_key, re.IGNORECASE)
        if not match:
            raise ValueError(f"Could not extract issue key from URL: {issue_key_or_url}")
        issue_key = match.group(1).upper()
        instance_channel = channel.get_instance_client(url=issue_key_or_url)

    adapter = JiraFormatAdapter(instance_channel)
    issue = await adapter.get_issue(
        issue_key=issue_key,
        format="raw",
        max_comments=None if include_all_comments else 5,
        include_comments=True,
        include_fields=None,
    )
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    comments = adapter._get_comments_list(issue if isinstance(issue, dict) else {}, None if include_all_comments else 5)
    comment_field = fields.get("comment", {})
    comments_total = int((comment_field or {}).get("total") or len(comments)) if isinstance(comment_field, dict) else len(comments)

    attachments = []
    text_attachments_total = 0
    text_attachments_loaded = 0
    text_attachments_full_loaded = 0
    text_attachments_preview_only = 0
    text_attachments_with_full_ref = 0
    partial_reasons: list[str] = []
    attachment_body_partial_reasons: list[str] = []
    attachment_list = fields.get("attachment", []) if isinstance(fields, dict) else []
    binary_attachments_count = 0
    binary_attachment_bodies_skipped_count = 0
    artifact_refs: list[dict] = []
    projectable_attachments_total = 0

    for att in attachment_list:
        mime = str(att.get("mimeType") or "")
        filename = str(att.get("filename") or "unknown")
        is_projectable = can_project_to_text(mime, filename)
        if is_projectable:
            text_attachments_total += 1
            projectable_attachments_total += 1
        else:
            binary_attachments_count += 1
            binary_attachment_bodies_skipped_count += 1
            partial_reasons.append(f"binary_attachment_body_skipped:{filename}")

        item = {
            "id": att.get("id"),
            "filename": filename,
            "mime_type": mime,
            "size": att.get("size", 0),
            "created": att.get("created"),
            "author": att.get("author"),
            "text_preview": None,
            "text_ref": None,
            "attachment_text_preview_only": False,
            "parse_status": None,
            "parse_error": None,
            "projected_to_text": False,
        }

        should_load_text_body = (
            include_attachments
            and attachment_body_policy == "source_complete"
            and is_projectable
            and att.get("content")
        )

        if should_load_text_body:
            try:
                auth_header = instance_channel._auth_header if instance_channel.is_configured() else None
                result = await downloader(
                    url=att.get("content"),
                    session_id=session_id,
                    options={"include_image_data": False},
                    auth_header=auth_header,
                    source_type="jira",
                    source_kind="issue_attachment",
                    source_locator=f"{issue_key}:{att.get('id')}",
                    provider_metadata={"issue_key": issue_key, "attachment_id": att.get("id")},
                    persist_text_ref_session_id=session_id,
                    persist_text_ref_kind="jira_attachment_text",
                    persist_text_ref_source_id=f"{issue_key}:{att.get('id')}",
                    persist_text_ref_title=f"Jira attachment text {filename}",
                    persist_text_ref_metadata={"issue_key": issue_key, "filename": filename, "attachment_id": att.get("id")},
                )
                item["parse_status"] = getattr(result, "parse_status", None)
                item["parse_error"] = getattr(result, "parse_error", None)
                item["projected_to_text"] = bool(getattr(result, "projected_to_text", False))
                if getattr(result, "parse_status", None) == "completed" and bool(getattr(result, "projected_to_text", False)):
                    text_content = str(getattr(result, "content", "") or "")
                    if len(text_content) <= 4000:
                        item["text_preview"] = text_content
                        text_attachments_full_loaded += 1
                    else:
                        item["text_preview"] = text_content[:1000]
                        item["attachment_text_preview_only"] = True
                        text_attachments_preview_only += 1
                        attachment_body_partial_reasons.append(f"text_attachment_preview_only:{filename}")
                    item["text_ref"] = getattr(result, "text_ref", None)
                    if item["text_ref"]:
                        text_attachments_with_full_ref += 1
                    text_attachments_loaded += 1
                else:
                    parse_reason = f"attachment_text_processing_failed:{filename}:parse_failed"
                    partial_reasons.append(parse_reason)
                    attachment_body_partial_reasons.append(
                        f"{parse_reason}:{item['parse_error']}" if item["parse_error"] else parse_reason
                    )
                if getattr(result, "artifact_id", None):
                    record = artifact_storage.get_artifact(result.artifact_id)
                    if record:
                        bind_artifact_to_source_bundle(record.artifact_id, f"jira:{issue_key}")
                        artifact_refs.append(build_artifact_ref_dict(record, text_ref=item.get("text_ref")))
            except Exception as exc:
                partial_reasons.append(f"attachment_text_processing_failed:{filename}:{type(exc).__name__}")
                attachment_body_partial_reasons.append(f"attachment_text_processing_failed:{filename}:{type(exc).__name__}")
        elif include_attachments and attachment_body_policy == "metadata_only" and is_projectable:
            partial_reasons.append(f"text_attachment_body_metadata_only:{filename}")
            attachment_body_partial_reasons.append(f"text_attachment_body_metadata_only:{filename}")
        attachments.append(item)

    rendered_fields = issue.get("renderedFields") if isinstance(issue, dict) else None
    bundle = {
        "issue_key": issue_key,
        "metadata": {
            "key": issue.get("key") if isinstance(issue, dict) else issue_key,
            "title": fields.get("summary"),
            "status": (fields.get("status") or {}).get("name") if isinstance(fields.get("status"), dict) else "",
            "type": (fields.get("issuetype") or {}).get("name") if isinstance(fields.get("issuetype"), dict) else "",
            "priority": (fields.get("priority") or {}).get("name") if isinstance(fields.get("priority"), dict) else "",
            "assignee": (fields.get("assignee") or {}).get("displayName") if isinstance(fields.get("assignee"), dict) else "",
        },
        "description": adapter._convert_description_to_markdown(fields.get("description")),
        "acceptance_criteria": adapter._extract_acceptance_criteria(issue if isinstance(issue, dict) else {}),
        "business_rules": "",
        "validation_rules": "",
        "comments": [
            {
                "id": c.get("id"),
                "author": c.get("author"),
                "created": c.get("created"),
                "body": c.get("body"),
                "body_markdown": adapter._convert_description_to_markdown(c.get("body")),
            }
            for c in comments
        ],
        "attachments": attachments,
        "artifact_refs": artifact_refs,
        "raw_snapshot": issue if include_raw_snapshot else {},
        "names": issue.get("names") if isinstance(issue, dict) else {},
        "renderedFields": issue.get("renderedFields") if isinstance(issue, dict) else {},
        "completeness_ledger": {
            "issue_loaded": bool(issue),
            "raw_issue_loaded": bool(issue),
            "names_loaded": bool(issue.get("names")) if isinstance(issue, dict) else False,
            "issue_fields_complete": bool(fields),
            "comments_loaded": len(comments),
            "comments_total": comments_total,
            "comments_complete": len(comments) >= comments_total,
            "attachments_metadata_loaded": len(attachments),
            "attachments_total": len(attachment_list),
            "attachments_metadata_complete": len(attachments) >= len(attachment_list),
            "attachment_metadata_complete": len(attachments) >= len(attachment_list),
            "text_attachments_loaded": text_attachments_loaded,
            "text_attachments_total": text_attachments_total,
            "projectable_attachments_total": projectable_attachments_total,
            "artifact_refs_created": len(artifact_refs),
            "text_attachments_complete": text_attachments_loaded >= text_attachments_total,
            "text_attachment_bodies_complete": (
                text_attachments_loaded >= text_attachments_total
                and text_attachments_with_full_ref >= text_attachments_preview_only
            ),
            "text_attachments_full_loaded": text_attachments_full_loaded,
            "text_attachments_preview_only": text_attachments_preview_only,
            "text_attachments_with_full_ref": text_attachments_with_full_ref,
            "binary_attachments_count": binary_attachments_count,
            "binary_attachment_bodies_available": False,
            "binary_attachment_bodies_skipped_count": binary_attachment_bodies_skipped_count,
            "binary_attachment_body_policy": "metadata_only" if binary_attachments_count > 0 else "loaded",
            "attachment_body_complete": text_attachments_preview_only == 0 and binary_attachment_bodies_skipped_count == 0,
            "attachment_body_partial_reasons": attachment_body_partial_reasons + [
                f"binary_attachment_body_skipped:{att.get('filename', 'unknown')}"
                for att in attachment_list
                if not can_project_to_text(str(att.get("mimeType") or ""), str(att.get("filename") or ""))
            ],
            "raw_fields_loaded": bool(fields),
            "rendered_fields_loaded": bool(rendered_fields),
            "custom_fields_loaded": bool(issue.get("names")) if isinstance(issue, dict) else False,
            "source_complete_definition": (
                "source_complete requires issue fields, all comments, attachment metadata, and text attachment bodies. "
                "Binary attachment bodies are metadata-only by design and do not block source_complete."
            ),
            "partial_reasons": partial_reasons,
        },
    }

    ledger = bundle["completeness_ledger"]
    ledger["source_metadata_complete"] = bool(
        ledger.get("issue_fields_complete")
        and ledger.get("names_loaded")
        and ledger.get("rendered_fields_loaded")
        and ledger.get("attachment_metadata_complete")
    )
    ledger["source_text_complete"] = bool(
        ledger.get("comments_complete")
        and ledger.get("text_attachment_bodies_complete")
    )
    ledger["source_tree_complete"] = True
    ledger["source_complete_for_generation"] = bool(
        ledger["source_metadata_complete"] and ledger["source_text_complete"]
    )
    ledger["source_complete_including_binary_bodies"] = bool(
        ledger["source_complete_for_generation"]
        and ledger.get("binary_attachment_bodies_available")
        and int(ledger.get("binary_attachment_bodies_skipped_count", 0)) == 0
    )
    blocking_partial_reasons = [r for r in ledger.get("partial_reasons", []) if not str(r).startswith("binary_attachment_body_skipped:")]
    ledger["source_complete"] = ledger["source_complete_for_generation"] and not blocking_partial_reasons
    ledger["source_complete_definition"] = (
        "source_complete_for_generation requires issue metadata, all comments, full text fields, and full text attachment bodies. "
        "Binary attachments are metadata-only by policy; source_complete_including_binary_bodies is false when binary bodies are unavailable."
    )

    if session_id:
        persisted = persist_jira_source_bundle_and_digest(session_id=session_id, issue_key=issue_key, bundle=bundle)
    else:
        persisted = {
            "context_ref": None,
            "digest_ref": None,
            "source_complete": ledger["source_complete"],
            "partial_reasons": list(ledger.get("partial_reasons") or []),
            "source_digest_chunk_count": 0,
        }
    apply_session_scope_requirement(
        ledger,
        has_context_ref=bool(persisted.get("context_ref")),
        has_digest_ref=bool(persisted.get("digest_ref")),
    )
    if persisted.get("context_ref") is None and persisted.get("digest_ref") is None:
        persisted["source_complete"] = ledger["source_complete"]
    refreshed_artifact_refs: list[dict] = []
    for ref in artifact_refs:
        artifact_id = ref.get("artifact_id")
        if not artifact_id:
            continue
        if persisted.get("context_ref") and persisted.get("digest_ref"):
            attach_source_refs_to_artifact(
                artifact_id,
                context_ref=persisted.get("context_ref"),
                digest_ref=persisted.get("digest_ref"),
            )
        record = artifact_storage.get_artifact(artifact_id)
        if record:
            refreshed_artifact_refs.append(build_artifact_ref_dict(record))
    if refreshed_artifact_refs:
        bundle["artifact_refs"] = refreshed_artifact_refs
        artifact_refs = refreshed_artifact_refs
    manifest = {
        "issue_key": issue_key,
        "title": (fields.get("summary") if isinstance(fields, dict) else "") or "",
        "context_ref": persisted.get("context_ref"),
        "digest_ref": persisted.get("digest_ref"),
        "source_complete": ledger["source_complete"],
        "source_complete_for_generation": ledger["source_complete_for_generation"],
        "source_complete_including_binary_bodies": ledger["source_complete_including_binary_bodies"],
        "source_metadata_complete": ledger["source_metadata_complete"],
        "source_text_complete": ledger["source_text_complete"],
        "attachment_metadata_complete": ledger["attachment_metadata_complete"],
        "text_attachment_bodies_complete": ledger["text_attachment_bodies_complete"],
        "source_tree_complete": ledger["source_tree_complete"],
        "binary_attachment_body_policy": ledger.get("binary_attachment_body_policy"),
        "binary_attachment_bodies_available": ledger.get("binary_attachment_bodies_available"),
        "binary_attachment_bodies_skipped_count": ledger.get("binary_attachment_bodies_skipped_count"),
        "source_complete_definition": ledger.get("source_complete_definition"),
        "comments_loaded": f"{ledger['comments_loaded']}/{ledger['comments_total']}",
        "attachments_metadata_loaded": f"{ledger['attachments_metadata_loaded']}/{ledger['attachments_total']}",
        "text_attachments_loaded": f"{ledger['text_attachments_loaded']}/{ledger['text_attachments_total']}",
        "binary_attachments_preserved": max(0, ledger["attachments_total"] - ledger["text_attachments_loaded"]),
        "partial_reasons": ledger["partial_reasons"],
        "source_digest_chunk_count": persisted.get("source_digest_chunk_count", 0),
        "sections": ["metadata", "description", "acceptance_criteria", "comments", "attachments", "raw_snapshot"],
    }

    return JiraIssueSourceResult(
        issue_key=issue_key,
        issue=issue if isinstance(issue, dict) else {},
        fields=fields if isinstance(fields, dict) else {},
        bundle=bundle,
        manifest=manifest,
        persisted=persisted,
        channel=instance_channel,
        adapter=adapter,
        attachment_list=list(attachment_list),
    )


def format_jira_source_manifest(result: JiraIssueSourceResult) -> str:
    lines = ["[jira source bundle prepared]"]
    for k, v in result.manifest.items():
        rendered = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
        lines.append(f"{k}: {rendered}")
    lines.append("")
    if result.persisted.get("context_ref"):
        lines.append(
            f'Use context_read_ref(ref="{result.persisted["context_ref"]}", section="...") to inspect source sections.'
        )
    else:
        lines.append("Use context_read_ref is unavailable because context_ref is None.")
    return "\n".join(lines)
