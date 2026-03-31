"""Jira exporter utilities - export issues to Markdown files or combined document.

This module implements a Python tool `export_issues_to_markdown` that follows
the jira-fields-to-markdown specification. It reuses the existing
`JiraFormatAdapter` and `jira_channel` to fetch issue data and converts
rich text to Markdown while preserving Acceptance Criteria and tables.
"""

import logging
import os
import re
import zipfile
import shutil
import asyncio
import time
from pathlib import Path

from src.utils.file_parser import get_file_path
from src.utils.file_parser.validators import sanitize_filename

# Attachment handling defaults
DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
DEFAULT_INLINE_TEXT_THRESHOLD = 2000  # chars
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = [1, 2, 4]
from typing import Any, Dict, List, Optional

from .api import jira_channel
from .adapter import JiraFormatAdapter
from src.utils.attachment import download_and_process_attachment

logger = logging.getLogger(__name__)


def _summary_to_slug(summary: str, max_len: int = 80) -> str:
    if not summary:
        return "untitled"
    s = summary.lower()
    # Replace non-alphanumeric with '-'
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = re.sub(r'-{2,}', '-', s).strip('-')
    if len(s) > max_len:
        s = s[:max_len].rstrip('-')
    return s or "untitled"


def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _write_file(path: str, content: str) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


async def _download_attachments(
    issue_key: str,
    attachments: List[dict],
    out_dir: str,
    auth_header: dict,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_download_size: int = DEFAULT_MAX_DOWNLOAD_SIZE,
    inline_text_threshold: int = DEFAULT_INLINE_TEXT_THRESHOLD,
    retries: int = DEFAULT_RETRIES,
    backoff: List[int] = None,
    preserve_binary: bool = True,
) -> List[str]:
    """Download attachments concurrently with retries, size checks, and inline text handling.

    Returns a list of strings which are either relative paths under out_dir or
    small preformatted markdown snippets for inline display.
    """
    results: List[str] = []
    _safe_mkdir(out_dir)

    semaphore = asyncio.Semaphore(concurrency)
    backoff_list = backoff if backoff is not None else DEFAULT_BACKOFF

    async def download_one(att: dict) -> str:
        filename = att.get('filename', 'unknown')
        url = att.get('content') or att.get('content', '')
        size = att.get('size') or 0
        if not url:
            return f"{filename} - no URL"

        # Size check
        if size and size > max_download_size:
            return f"{filename} - skipped (size {size} > {max_download_size})"

        attempt = 0
        last_exc = None
        while attempt < retries:
            try:
                async with semaphore:
                    res = await download_and_process_attachment(
                        url=url,
                        session_id=f"jira-{issue_key}",
                        options={"include_image_data": True},
                        auth_header=auth_header,
                    )

                # If helper saved the file and returned file_id, copy stored file
                if hasattr(res, 'file_id') and getattr(res, 'file_id', None):
                    try:
                        src_path = str(get_file_path(res.file_id))
                        out_filename = sanitize_filename(res.filename or filename)
                        target_path = os.path.join(out_dir, out_filename)
                        base, ext = os.path.splitext(target_path)
                        idx = 1
                        while os.path.exists(target_path):
                            target_path = f"{base}-{idx}{ext}"
                            idx += 1
                        shutil.copyfile(src_path, target_path)
                        return os.path.relpath(target_path, start=out_dir)
                    except Exception as e:
                        logger.warning(f"Failed to copy stored file for attachment {filename}: {e}")
                        return f"{filename} - stored copy failed: {e}"

                # Inline content handling
                content_format = getattr(res, 'content_format', None)
                content = getattr(res, 'content', None)

                out_filename = sanitize_filename(filename)
                target_path = os.path.join(out_dir, out_filename)
                base, ext = os.path.splitext(target_path)
                idx = 1
                while os.path.exists(target_path):
                    target_path = f"{base}-{idx}{ext}"
                    idx += 1

                if content_format == 'text' and content:
                    text = content.decode('utf-8') if isinstance(content, bytes) else content
                    # if small, return preformatted markdown snippet with inline content
                    if len(text) <= DEFAULT_INLINE_TEXT_THRESHOLD:
                        # Save file and also return inline snippet
                        with open(target_path, 'w', encoding='utf-8') as f:
                            f.write(text)
                        snippet = f"{out_filename}\n\n```text\n{text}\n```"
                        return snippet
                    else:
                        with open(target_path, 'w', encoding='utf-8') as f:
                            f.write(text)
                        return os.path.relpath(target_path, start=out_dir)

                if content_format == 'base64' and content:
                    # Save base64 blob to .b64 file
                    with open(target_path + '.b64', 'wb') as f:
                        blob = content if isinstance(content, bytes) else content.encode('utf-8')
                        f.write(blob)
                    return os.path.relpath(target_path + '.b64', start=out_dir)

                # Unknown content: create metadata note
                note_path = target_path + '.txt'
                with open(note_path, 'w', encoding='utf-8') as f:
                    f.write(f"Attachment: {filename}\nSource: {url}\nContent format: {content_format}\n")
                return os.path.relpath(note_path, start=out_dir)

            except Exception as e:
                last_exc = e
                logger.debug(f"Attachment download attempt {attempt+1} failed for {filename}: {e}")
                # Backoff
                if attempt < len(backoff_list):
                    await asyncio.sleep(backoff_list[attempt])
                else:
                    await asyncio.sleep(backoff_list[-1])
                attempt += 1

        # If all attempts failed
        return f"{filename} - download failed: {last_exc}"

    # Launch downloads concurrently
    tasks = [asyncio.create_task(download_one(att)) for att in attachments]
    completed = await asyncio.gather(*tasks)
    results.extend(completed)
    return results


async def export_issues_to_markdown(
    input: Any,
    output_mode: str = "single_combined",
    output_directory: Optional[str] = None,
    download_attachments: Optional[bool] = None,
    attachments_dir: str = "attachments",
    include_raw_snapshot: bool = False,
    max_comments: int = 10,
    comments_order: str = "latest_first",
    field_match_threshold: float = 0.9,
    field_similarity_threshold: float = 0.9,
    array_inline_max_items: int = 3,
    array_inline_max_element_length: int = 40,
    # Attachment handling tunables
    attachments_concurrency: int = DEFAULT_CONCURRENCY,
    attachments_max_size: int = DEFAULT_MAX_DOWNLOAD_SIZE,
    attachments_inline_text_threshold: int = DEFAULT_INLINE_TEXT_THRESHOLD,
    attachments_retries: int = DEFAULT_RETRIES,
    attachments_backoff: Optional[List[int]] = None,
    attachments_preserve_binary: bool = True,
) -> Dict[str, Any]:
    """Export Jira issues (single, list, or JQL) to Markdown.

    Returns a dict {"success": [filepaths or issue_keys], "errors": [{issue_key, error}]}
    """
    # Validate output_mode
    if output_mode not in ("single_combined", "one_file_per_issue", "zip_per_issue"):
        return {"success": [], "errors": [{"issue_key": None, "error": f"Invalid output_mode: {output_mode}"}]}

    if output_mode in ("one_file_per_issue", "zip_per_issue") and not output_directory:
        return {"success": [], "errors": [{"issue_key": None, "error": "output_directory is required for file output modes"}]}

    # Determine default for download_attachments
    if download_attachments is None:
        download_attachments = bool(output_directory)

    adapter = JiraFormatAdapter(jira_channel)

    # Determine issue keys list
    issue_keys: List[str] = []
    errors: List[Dict[str, str]] = []
    successes: List[str] = []

    # Helper to add error
    def _add_error(key, msg):
        errors.append({"issue_key": key, "error": msg})

    # Standardize input, support string 'jql:...' auto convert to dict
    if isinstance(input, str) and input.strip().lower().startswith('jql:'):
        input = {"jql": input.strip()[4:].strip()}

    try:
        # Input can be: string key, comma separated, or dict with jql
        if isinstance(input, dict) and input.get("jql"):
            jql = input.get("jql")
            # Use channel search to paginate (default page size 50)
            start_at = 0
            page_size = input.get("page_size", 50)
            while True:
                result = await jira_channel.search_issues(jql, max_results=page_size, start_at=start_at)
                issues = result.get("issues", [])
                if not issues:
                    break
                for issue in issues:
                    key = issue.get("key")
                    if key:
                        issue_keys.append(key)
                start_at += len(issues)
                total = result.get("total", 0)
                if start_at >= total:
                    break
        elif isinstance(input, str):
            # comma separated or single
            parts = [p.strip() for p in input.split(",") if p.strip()]
            issue_keys.extend(parts)
        elif isinstance(input, list):
            for p in input:
                if isinstance(p, str):
                    issue_keys.append(p.strip())
        else:
            return {"success": [], "errors": [{"issue_key": None, "error": "Unsupported input type"}]}
    except Exception as e:
        return {"success": [], "errors": [{"issue_key": None, "error": f"Failed to resolve input: {e}"}]}

    if not issue_keys:
        return {"success": [], "errors": [{"issue_key": None, "error": "No issue keys resolved from input"}]}

    combined_lines: List[str] = []

    # Ensure output dir exists
    if output_directory:
        _safe_mkdir(output_directory)

    for key in issue_keys:
        try:
            # Fetch raw issue with expanded names and renderedFields for better extraction
            issue = await jira_channel.get_issue(key, expand=["names", "renderedFields"])

            # Ensure comments loaded (must use return value!)
            try:
                issue = await adapter._ensure_comments_loaded(key, issue, max_comments)
            except Exception:
                logger.debug(f"Failed to ensure comments loaded for {key}")

            fields = issue.get("fields", {})

            summary = fields.get("summary", "")
            description_md = adapter._convert_description_to_markdown(fields.get("description"))
            acceptance = adapter._extract_acceptance_criteria(issue) or "N/A"

            # Comments
            comments = adapter._get_comments_list(issue, max_comments)
            if comments_order == "latest_first":
                comments = comments[:max_comments]
            else:
                comments = list(reversed(comments))[:max_comments]

            # Attachments
            attachments = fields.get("attachment", []) or []

            # Build markdown using strict template
            md_lines = []
            md_lines.append(f"# {key}: {summary}\n")
            md_lines.append("## Description")
            md_lines.append(description_md or "N/A")
            md_lines.append("\n## Acceptance Criteria")
            md_lines.append(acceptance)
            md_lines.append("\n## Comments")
            if comments:
                for i, c in enumerate(comments, 1):
                    author = c.get('author') if isinstance(c.get('author'), str) else c.get('author', {}).get('displayName', 'Unknown')
                    created = c.get('created', '')[:10] if c.get('created') else 'N/A'
                    body_md = adapter._convert_description_to_markdown(c.get('body'))
                    md_lines.append(f"### {i}) {author} - {created}\n{body_md}\n")
            else:
                md_lines.append("N/A")

            # Attachments handling and optional downloads
            md_lines.append("\n## Attachments")
            attachment_entries: List[str] = []
            downloaded_files: List[str] = []
            if attachments:
                if download_attachments and output_directory:
                    # Download into output_directory/attachments/<issue_key>/
                    att_out_dir = os.path.join(output_directory, attachments_dir, key)
                    auth_header = jira_channel._auth_header if jira_channel.is_configured() else None
                    downloaded = await _download_attachments(
                        key,
                        attachments,
                        att_out_dir,
                        auth_header,
                        concurrency=attachments_concurrency,
                        max_download_size=attachments_max_size,
                        inline_text_threshold=attachments_inline_text_threshold,
                        retries=attachments_retries,
                        backoff=attachments_backoff,
                        preserve_binary=attachments_preserve_binary,
                    )
                    for d in downloaded:
                        attachment_entries.append(f"- {d}")
                        downloaded_files.append(os.path.join(att_out_dir, d) if not d.endswith(' - no URL') and 'download failed' not in d else d)
                else:
                    for att in attachments:
                        filename = att.get('filename', 'unknown')
                        url = att.get('content') or att.get('content', '')
                        attachment_entries.append(f"- {filename} ({url})")
            else:
                attachment_entries.append("N/A")

            md_lines.extend(attachment_entries)

            # Raw snapshot
            if include_raw_snapshot:
                md_lines.append("\n## Raw Fields Snapshot")
                md_lines.append(str({"key": key, "fields": {k: v for k, v in fields.items() if k in ['summary','status','description','attachment']}}))

            markdown = "\n".join(md_lines)

            # Output modes
            if output_mode == "single_combined":
                combined_lines.append(markdown)
                successes.append(key)
            else:
                # file per issue
                slug = _summary_to_slug(summary)
                filename = f"{key} - {slug}.md"
                dest = os.path.join(output_directory, filename)
                # handle collisions
                base, ext = os.path.splitext(dest)
                idx = 1
                while os.path.exists(dest):
                    dest = f"{base}-{idx}{ext}"
                    idx += 1
                _write_file(dest, markdown)
                successes.append(dest)

        except Exception as e:
            logger.exception(f"Failed to export {key}")
            _add_error(key, str(e))

    # finalize results
    result: Dict[str, Any] = {"success": successes, "errors": errors}

    if output_mode == "single_combined":
        combined = "\n\n---\n\n".join(combined_lines)
        if output_directory:
            dest = os.path.join(output_directory, "jira-export.md")
            _write_file(dest, combined)
            result["export_path"] = dest
            result["success"] = [dest]
        else:
            result["content"] = combined

    if output_mode == "zip_per_issue" and output_directory and successes:
        zip_path = os.path.join(output_directory, "jira-export.zip")
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for path in successes:
                if os.path.exists(path):
                    arcname = os.path.relpath(path, start=output_directory)
                    zf.write(path, arcname=arcname)
        result["zip"] = zip_path

    return result


__all__ = ["export_issues_to_markdown"]

