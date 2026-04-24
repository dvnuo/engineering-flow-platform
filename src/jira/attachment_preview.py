from __future__ import annotations

import logging
from typing import Optional

from src.utils.attachment import download_and_process_attachment

from .api import jira_channel

logger = logging.getLogger(__name__)


async def render_issue_attachment_previews(
    issue_key: str,
    attachments: list[dict],
    *,
    session_id: str | None = None,
) -> str:
    attachment_list = attachments or []
    if not attachment_list:
        return ""

    logger.info("Processing %s attachments for %s", len(attachment_list), issue_key)
    results: list[str] = []

    for att in attachment_list[:5]:
        filename = str(att.get("filename") or "unknown")
        mime_type = str(att.get("mimeType") or "application/octet-stream")
        size = int(att.get("size") or 0)
        content_url = att.get("content")
        attachment_id = att.get("id")

        if not content_url:
            results.append(f"- **{filename}** ({mime_type}, {size} bytes)")
            continue

        try:
            auth_header = jira_channel._auth_header if jira_channel.is_configured() else None
            result = await download_and_process_attachment(
                url=content_url,
                session_id=session_id,
                options={"include_image_data": True},
                auth_header=auth_header,
                source_type="jira",
                source_kind="issue_attachment",
                source_locator=f"{issue_key}:{attachment_id}",
                provider_metadata={"issue_key": issue_key, "attachment_id": attachment_id, "filename": filename},
                persist_text_ref_session_id=session_id,
                persist_text_ref_kind="jira_attachment_text",
                persist_text_ref_source_id=f"{issue_key}:{attachment_id}",
                persist_text_ref_title=f"Jira attachment text {filename}",
                persist_text_ref_metadata={"issue_key": issue_key, "attachment_id": attachment_id, "filename": filename},
            )

            if result.content_format == "base64":
                results.append(f"- **{filename}** (image, {size} bytes)")
                results.append(f"  {result.content}")
            elif result.content and result.content_format in {"text", "metadata"}:
                preview = str(result.content)[:500]
                results.append(f"- **{filename}** ({mime_type}, {size} bytes)")
                results.append(f"  {preview}")
            else:
                results.append(f"- **{filename}** ({mime_type}, {size} bytes)")

            results.append(f"  artifact_id: {getattr(result, 'artifact_id', None)}")
            results.append(f"  text_ref: {getattr(result, 'text_ref', None)}")
            results.append(f"  parse_status: {getattr(result, 'parse_status', None)}")
            parse_error = getattr(result, "parse_error", None)
            if parse_error:
                results.append(f"  parse_error: {parse_error}")
        except Exception as e:
            logger.warning("Failed to process attachment %s: %s", filename, e)
            results.append(f"- **{filename}** ({mime_type}, {size} bytes) - [processing failed]")
            results.append("  parse_status: failed")
            results.append(f"  parse_error: {type(e).__name__}")

    if results:
        return "**Attachments:**\n" + "\n".join(results) + "\n"
    return ""
