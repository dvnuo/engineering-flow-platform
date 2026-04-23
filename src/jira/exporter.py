"""Canonical Jira markdown exporter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.attachment import download_and_process_attachment
from src.utils.file_parser import get_file_path
from src.utils.file_parser.validators import sanitize_filename

from .api import jira_channel
from .markdown_renderer import render_jira_issue_export_markdown
from .selector import extract_output_directory_from_text, normalize_jira_issue_selector
from .source_service import prepare_jira_issue_source

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4
DEFAULT_MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024
DEFAULT_INLINE_TEXT_THRESHOLD = 2000
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = [1, 2, 4]


def _summary_to_slug(summary: str, max_len: int = 80) -> str:
    if not summary:
        return "untitled"
    s = summary.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "untitled"


def _allowed_workspace_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.getenv("EFP_WORKSPACE_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.append(Path.home() / ".efp" / "workspace")
    roots.append(Path("/root/.efp/workspace"))

    resolved: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            r = root.resolve()
        except Exception:
            r = root.expanduser().absolute()
        key = str(r)
        if key not in seen:
            seen.add(key)
            resolved.append(r)
    return resolved


def _resolve_output_directory(output_directory: str) -> Path:
    output = Path(output_directory).expanduser().resolve()
    allowed_roots = _allowed_workspace_roots()
    if not any(output == root or root in output.parents for root in allowed_roots):
        allowed = ", ".join(str(root) for root in allowed_roots)
        raise ValueError(f"output_directory must be under an EFP workspace root: {allowed}")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _safe_issue_markdown_filename(issue_key: str, summary: str) -> str:
    issue_part = sanitize_filename(issue_key.upper())
    slug = sanitize_filename(_summary_to_slug(summary or "untitled"))
    return f"{issue_part} - {slug}.md"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    base = path.with_suffix("")
    suffix = path.suffix
    idx = 1
    while True:
        candidate = Path(f"{base}-{idx}{suffix}")
        if not candidate.exists():
            return candidate
        idx += 1


def _sanitize_attachments_dir(attachments_dir: str) -> str:
    raw = str(attachments_dir or "attachments").strip()
    if not raw:
        return "attachments"
    p = Path(raw)
    if p.is_absolute():
        raise ValueError("attachments_dir must be a relative directory name")
    if any(part in {"..", ""} for part in p.parts):
        raise ValueError("attachments_dir must not contain path traversal")
    if len(p.parts) != 1:
        raise ValueError("attachments_dir must be a simple directory name")
    safe = sanitize_filename(raw)
    if not safe or safe in {".", ".."}:
        raise ValueError("attachments_dir is invalid")
    return safe


def _resolve_attachment_issue_dir(output_dir: Path, attachments_dir: str, issue_key: str) -> tuple[Path, str]:
    safe_attachments_dir = _sanitize_attachments_dir(attachments_dir)
    safe_issue_key = sanitize_filename(issue_key.upper())
    issue_dir = (output_dir / safe_attachments_dir / safe_issue_key).resolve()
    output_root = output_dir.resolve()
    if issue_dir != output_root and output_root not in issue_dir.parents:
        raise ValueError("attachment directory must stay under output_directory")
    issue_dir.mkdir(parents=True, exist_ok=True)
    return issue_dir, safe_attachments_dir


def _zip_export_paths(export_dir: Path, zip_path: Path, paths: list[Path]) -> None:
    export_root = export_dir.resolve()
    normalized: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        if not p:
            continue
        rp = Path(p).resolve()
        if not rp.exists() or not rp.is_file():
            continue
        if rp == zip_path.resolve():
            continue
        if export_root not in rp.parents and rp != export_root:
            continue
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(rp)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in normalized:
            zf.write(file_path, arcname=file_path.relative_to(export_root).as_posix())


def _is_probably_text_attachment(att: dict, result: Any) -> bool:
    mime = str((att or {}).get("mimeType") or getattr(result, "content_type", "") or "").lower()
    filename = str((att or {}).get("filename") or getattr(result, "filename", "") or "").lower()
    return mime.startswith("text/") or filename.endswith((".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".log"))


async def _download_issue_attachments(
    issue_key: str,
    attachment_list: list[dict],
    *,
    output_dir: Path,
    attachments_dir: str,
    concurrency: int,
    attachments_max_size: int,
    attachments_inline_text_threshold: int,
    attachments_retries: int,
    attachments_backoff: Optional[List[int]],
    attachments_preserve_binary: bool,
    source_channel: Any = None,
) -> list[dict]:
    channel = source_channel or jira_channel
    auth_header = channel._auth_header if channel and channel.is_configured() else None
    issue_dir, safe_attachments_dir = _resolve_attachment_issue_dir(output_dir, attachments_dir, issue_key)
    sem = asyncio.Semaphore(max(1, int(concurrency or 1)))
    backoff = attachments_backoff or DEFAULT_BACKOFF

    async def _handle(att: dict) -> dict:
        filename = str(att.get("filename") or "unknown")
        size = int(att.get("size") or 0)
        mime_type = str(att.get("mimeType") or "")
        url = att.get("content")
        item = {
            "filename": filename,
            "status": "failed",
            "path": None,
            "absolute_path": None,
            "size": size,
            "mime_type": mime_type,
        }

        if not url:
            item["status"] = "skipped"
            item["reason"] = "missing_content_url"
            return item

        if size and size > attachments_max_size:
            item["status"] = "skipped"
            item["reason"] = f"size_exceeds_limit:{size}>{attachments_max_size}"
            return item

        last_error = None
        for attempt in range(max(1, int(attachments_retries or 1))):
            try:
                async with sem:
                    result = await download_and_process_attachment(
                        url=url,
                        session_id=f"jira-export-{issue_key}",
                        options={"include_image_data": True},
                        auth_header=auth_header,
                    )

                is_text = _is_probably_text_attachment(att, result)
                if not is_text and not attachments_preserve_binary:
                    item["status"] = "skipped"
                    item["reason"] = "binary_preserve_disabled"
                    return item

                src = get_file_path(result.file_id)
                target_name = sanitize_filename(getattr(result, "filename", "") or filename)
                if not target_name:
                    target_name = "attachment"
                candidate_path = _unique_path(issue_dir / target_name).resolve()
                output_root = output_dir.resolve()
                if output_root not in candidate_path.parents:
                    raise ValueError("attachment target path escaped output_directory")
                if issue_dir not in candidate_path.parents and candidate_path.parent != issue_dir:
                    raise ValueError("attachment target path escaped issue attachment directory")
                shutil.copyfile(src, candidate_path)

                rel_path = candidate_path.relative_to(output_root).as_posix()
                item.update(
                    {
                        "status": "saved",
                        "path": rel_path,
                        "absolute_path": str(candidate_path),
                        "size": int(getattr(result, "metadata", {}).get("size") or size or candidate_path.stat().st_size),
                        "mime_type": getattr(result, "content_type", mime_type),
                    }
                )

                if getattr(result, "content_format", None) == "text":
                    content = getattr(result, "content", "") or ""
                    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
                    if len(text) <= attachments_inline_text_threshold:
                        item["inline_text"] = text

                return item
            except Exception as exc:
                last_error = exc
                sleep_sec = backoff[min(attempt, len(backoff) - 1)] if backoff else 1
                await asyncio.sleep(max(0, sleep_sec))

        item["status"] = "failed"
        item["reason"] = f"download_failed:{type(last_error).__name__}:{last_error}"
        return item

    return await asyncio.gather(*[_handle(att) for att in attachment_list])


async def export_issues_to_markdown(
    input: Any = None,
    *,
    issue_keys: Optional[List[str]] = None,
    jql: Optional[str] = None,
    page_size: int = 50,
    max_issues: int = 100,
    output_mode: str = "auto",
    output_directory: Optional[str] = None,
    download_attachments: Optional[bool] = None,
    attachments_dir: str = "attachments",
    include_raw_snapshot: bool = False,
    include_coverage_ledger: bool = True,
    max_comments: Optional[int] = 10,
    comments_order: str = "latest_first",
    attachments_concurrency: int = DEFAULT_CONCURRENCY,
    attachments_max_size: int = DEFAULT_MAX_DOWNLOAD_SIZE,
    attachments_inline_text_threshold: int = DEFAULT_INLINE_TEXT_THRESHOLD,
    attachments_retries: int = DEFAULT_RETRIES,
    attachments_backoff: Optional[List[int]] = None,
    attachments_preserve_binary: bool = True,
    _session_id: Optional[str] = None,
) -> Dict[str, Any]:
    run_id = f"jira-export-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    errors: list[str] = []

    if not output_directory and isinstance(input, str):
        output_directory = extract_output_directory_from_text(input)

    selector = normalize_jira_issue_selector(
        input=input,
        issue_keys=issue_keys,
        jql=jql,
        page_size=page_size,
        max_issues=max_issues,
    )

    selected_keys = list(selector.get("issue_keys") or [])
    if selector.get("selector_type") == "jql":
        jql_query = selector.get("jql")
        start_at = 0
        all_keys: list[str] = []
        while True:
            page = await jira_channel.search_issues(jql_query, max_results=selector["page_size"], start_at=start_at)
            issues = page.get("issues") or []
            if not issues:
                break
            all_keys.extend([i.get("key") for i in issues if i.get("key")])
            start_at += len(issues)
            if len(all_keys) >= selector["max_issues"]:
                selector["truncated"] = True
                selector["partial_reasons"].append(f"max_issues_truncated:{selector['max_issues']}")
                all_keys = all_keys[: selector["max_issues"]]
                break
            if start_at >= int(page.get("total") or 0):
                break
        selected_keys = all_keys

    if not selected_keys:
        errors.append("No Jira issues matched the selector.")
        return {
            "success": False,
            "status": "failed",
            "run_id": run_id,
            "selector": selector,
            "output_mode": output_mode,
            "output_directory": output_directory,
            "issues": [],
            "artifacts": {},
            "errors": errors,
        }

    resolved_output_mode = output_mode
    zip_after_export = False
    if output_mode == "auto":
        resolved_output_mode = "one_file_per_issue" if output_directory else "single_combined"
    elif output_mode == "zip":
        resolved_output_mode = "one_file_per_issue"
        zip_after_export = True
    elif output_mode not in {"single_combined", "one_file_per_issue"}:
        raise ValueError(f"Invalid output_mode: {output_mode}")

    output_dir_path: Optional[Path] = None
    if output_directory:
        output_dir_path = _resolve_output_directory(output_directory)

    if download_attachments is None:
        download_attachments = bool(output_dir_path)

    issues_out = []
    combined_chunks: list[str] = []
    artifacts: dict[str, Any] = {}
    created_paths: list[Path] = []

    for key in selected_keys:
        issue_result = {
            "issue_key": key,
            "status": "failed",
            "markdown_path": None,
            "attachments": [],
            "context_ref": None,
            "digest_ref": None,
            "source_complete": False,
            "source_complete_for_generation": False,
            "source_partial_reasons": [],
            "export_partial_reasons": [],
            "partial_reasons": [],
            "attachment_download_complete": True,
        }
        try:
            source = await prepare_jira_issue_source(
                issue_key_or_url=key,
                include_all_comments=True,
                include_attachments=True,
                include_raw_snapshot=include_raw_snapshot,
                session_id=_session_id or "unknown_session",
                attachment_body_policy="metadata_only" if download_attachments else "source_complete",
            )
            issue_result["context_ref"] = source.manifest.get("context_ref")
            issue_result["digest_ref"] = source.manifest.get("digest_ref")
            issue_result["source_complete"] = bool(source.manifest.get("source_complete"))
            issue_result["source_complete_for_generation"] = bool(source.manifest.get("source_complete_for_generation"))
            issue_result["source_partial_reasons"] = list(source.manifest.get("partial_reasons") or [])

            downloaded = []
            if download_attachments and output_dir_path:
                downloaded = await _download_issue_attachments(
                    key,
                    source.attachment_list,
                    output_dir=output_dir_path,
                    attachments_dir=attachments_dir,
                    concurrency=attachments_concurrency,
                    attachments_max_size=attachments_max_size,
                    attachments_inline_text_threshold=attachments_inline_text_threshold,
                    attachments_retries=attachments_retries,
                    attachments_backoff=attachments_backoff,
                    attachments_preserve_binary=attachments_preserve_binary,
                    source_channel=source.channel,
                )

            export_partial_reasons = []
            for att in downloaded:
                if att.get("status") in {"failed", "skipped"}:
                    filename = att.get("filename") or "unknown"
                    reason = att.get("reason") or "unknown"
                    export_partial_reasons.append(f"attachment_{att.get('status')}:{filename}:{reason}")
                if att.get("status") == "saved" and att.get("absolute_path"):
                    created_paths.append(Path(att["absolute_path"]))

            issue_result["export_partial_reasons"] = export_partial_reasons
            issue_result["partial_reasons"] = export_partial_reasons
            issue_result["attachment_download_complete"] = not export_partial_reasons

            markdown = render_jira_issue_export_markdown(
                source,
                downloaded_attachments=downloaded if downloaded else None,
                include_raw_snapshot=include_raw_snapshot,
                include_coverage_ledger=include_coverage_ledger,
                max_comments=max_comments,
                comments_order=comments_order,
            )

            issue_result["attachments"] = downloaded
            summary = source.fields.get("summary") or source.bundle.get("metadata", {}).get("title") or ""
            if resolved_output_mode == "one_file_per_issue":
                if not output_dir_path:
                    raise ValueError("output_directory is required for one_file_per_issue export")
                md_path = _unique_path(output_dir_path / _safe_issue_markdown_filename(key, summary))
                md_path.write_text(markdown, encoding="utf-8")
                created_paths.append(md_path)
                issue_result["markdown_path"] = str(md_path)
            else:
                combined_chunks.append(markdown)
            issue_result["status"] = "exported"
        except Exception as exc:
            issue_result["status"] = "failed"
            msg = f"{key}: {type(exc).__name__}: {exc}"
            issue_result["export_partial_reasons"].append(msg)
            issue_result["partial_reasons"].append(msg)
            issue_result["attachment_download_complete"] = False
            errors.append(msg)
        issues_out.append(issue_result)

    if resolved_output_mode == "single_combined":
        combined = "\n\n---\n\n".join(combined_chunks)
        if output_dir_path:
            combined_path = _unique_path(output_dir_path / "jira-export.md")
            combined_path.write_text(combined, encoding="utf-8")
            created_paths.append(combined_path)
            artifacts["combined_markdown_path"] = str(combined_path)
        else:
            artifacts["combined_content"] = combined

    warnings: list[str] = []
    if selector.get("truncated"):
        warnings.extend(selector.get("partial_reasons") or [])
    for issue in issues_out:
        for reason in issue.get("export_partial_reasons") or []:
            warnings.append(f"{issue.get('issue_key')}: {reason}")

    if output_dir_path:
        manifest_path = output_dir_path / "manifest.json"
        artifacts["manifest_path"] = str(manifest_path)

        if zip_after_export:
            zip_path = _unique_path(output_dir_path / "jira-export.zip")
            artifacts["zip_path"] = str(zip_path)

            final_manifest = {
                "run_id": run_id,
                "selector": selector,
                "output_mode": output_mode,
                "resolved_output_mode": resolved_output_mode,
                "output_directory": str(output_dir_path),
                "issues": issues_out,
                "artifacts": artifacts,
                "warnings": warnings,
                "errors": errors,
            }
            manifest_path.write_text(json.dumps(final_manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            created_paths.append(manifest_path)
            _zip_export_paths(output_dir_path, zip_path, created_paths)
        else:
            final_manifest = {
                "run_id": run_id,
                "selector": selector,
                "output_mode": output_mode,
                "resolved_output_mode": resolved_output_mode,
                "output_directory": str(output_dir_path),
                "issues": issues_out,
                "artifacts": artifacts,
                "warnings": warnings,
                "errors": errors,
            }
            manifest_path.write_text(json.dumps(final_manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            created_paths.append(manifest_path)

    exported_count = sum(1 for x in issues_out if x["status"] == "exported")
    failed_count = len(issues_out) - exported_count
    has_export_warnings = bool(selector.get("truncated")) or any(
        x.get("export_partial_reasons") for x in issues_out
    )

    if exported_count == 0:
        status = "failed"
    elif failed_count > 0 or has_export_warnings:
        status = "partial"
    else:
        status = "success"

    return {
        "success": status in {"success", "partial"},
        "status": status,
        "run_id": run_id,
        "selector": selector,
        "output_mode": output_mode,
        "output_directory": str(output_dir_path) if output_dir_path else None,
        "issues": issues_out,
        "artifacts": artifacts,
        "warnings": warnings,
        "errors": errors,
    }


__all__ = ["export_issues_to_markdown"]
