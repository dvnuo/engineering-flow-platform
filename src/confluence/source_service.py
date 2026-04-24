from __future__ import annotations

import json
import logging
from typing import Optional

from src.file_artifacts import can_project_to_text
from src.file_artifacts.service import attach_source_refs_to_artifact, bind_artifact_to_source_bundle, build_artifact_ref_dict
from src.source_context import persist_confluence_source_bundle_and_digest
from src.utils.attachment import download_and_process_attachment

from .adapter import ConfluenceFormatAdapter, _extract_page_id_from_url
from .api import ConfluenceChannel, confluence_channel

logger = logging.getLogger(__name__)


def _infer_attachment_media_type(att: dict, filename: str) -> str:
    media_type = att.get("metadata", {}).get("mediaType")
    if media_type:
        return media_type
    media_type = att.get("extensions", {}).get("mediaType")
    if media_type:
        return media_type
    extension_to_media_type = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
        "svg": "image/svg+xml",
        "txt": "text/plain",
        "md": "text/markdown",
        "json": "application/json",
        "csv": "text/csv",
        "pdf": "application/pdf",
    }
    if "." in filename:
        extension = filename.rsplit(".", 1)[-1].lower()
        return extension_to_media_type.get(extension, "application/octet-stream")
    return "application/octet-stream"


def _is_image_attachment(att: dict, filename: str) -> bool:
    return _infer_attachment_media_type(att, filename).startswith("image/")


async def prepare_confluence_page_source(
    page_id_or_url: str,
    include_children: bool = True,
    include_raw_snapshot: bool = True,
    attachment_body_policy: str = "source_complete",
    session_id: str | None = None,
    include_comments: bool = True,
    include_attachments: bool = True,
    channel: Optional[ConfluenceChannel] = None,
    downloader=download_and_process_attachment,
    persist_fn=persist_confluence_source_bundle_and_digest,
) -> dict:
    resolved_channel = channel or confluence_channel
    if not resolved_channel.is_configured():
        raise RuntimeError("Confluence is not configured. Please check your settings.")

    target = str(page_id_or_url or "").strip()
    page_id = target
    instance_channel = resolved_channel
    if target.startswith("http://") or target.startswith("https://"):
        extracted = _extract_page_id_from_url(target)
        if not extracted:
            raise ValueError(f"Could not extract page ID from URL: {page_id_or_url}")
        page_id = extracted
        instance_channel = resolved_channel.get_instance_client(url=target) or resolved_channel

    page = await instance_channel.get_page(page_id)
    comments, comments_ledger = await instance_channel.get_all_comments_with_ledger(page_id) if include_comments else ([], {"loaded": 0, "total": 0, "complete": False})
    attachments, attachments_ledger = await instance_channel.get_all_attachments_with_ledger(page_id) if include_attachments else ([], {"loaded": 0, "total": 0, "complete": False})
    children, children_ledger = await instance_channel.get_all_page_children_with_ledger(page_id) if include_children else ([], {"loaded": 0, "total": 0, "complete": False})

    descendants: list = []
    descendants_ledger: dict = {"loaded": 0, "total": 0, "complete": False, "partial_reasons": []}
    descendants_supported = bool(include_children)
    if include_children:
        try:
            descendants, descendants_ledger = await instance_channel.get_all_descendants_with_ledger(page_id)
        except Exception as desc_exc:
            descendants_supported = False
            descendants = []
            descendants_ledger = {
                "loaded": 0,
                "total": 0,
                "complete": False,
                "partial_reasons": [f"descendants_fetch_failed:{type(desc_exc).__name__}"],
            }

    partial_reasons = []
    comments = comments or []
    attachments = attachments or []
    children = children or []
    if include_comments and not isinstance(comments, list):
        comments = []
        partial_reasons.append("comments_unavailable")
    if include_attachments and not isinstance(attachments, list):
        attachments = []
        partial_reasons.append("attachments_unavailable")
    if include_children and not isinstance(children, list):
        children = []
        partial_reasons.append("children_unavailable")
    if include_children and not isinstance(descendants, list):
        descendants = []
        partial_reasons.append("descendants_unavailable")
    if not include_comments:
        partial_reasons.append("comments_not_requested")
    if not include_attachments:
        partial_reasons.append("attachments_not_requested")
    if not include_children:
        partial_reasons.append("children_not_requested")

    ledger = {
        "page_body_complete": bool(page),
        "comments_loaded": int(comments_ledger.get("loaded", len(comments))),
        "comments_total": int(comments_ledger.get("total", len(comments))),
        "comments_complete": bool(comments_ledger.get("complete", False)),
        "attachments_loaded": int(attachments_ledger.get("loaded", len(attachments))),
        "attachments_total": int(attachments_ledger.get("total", len(attachments))),
        "attachments_complete": bool(attachments_ledger.get("complete", False)),
        "artifact_refs_created": 0,
        "projectable_attachments_total": 0,
        "children_loaded": int(children_ledger.get("loaded", len(children))),
        "children_total": int(children_ledger.get("total", len(children))),
        "children_complete": bool(children_ledger.get("complete", False)),
        "descendants_loaded": int((descendants_ledger or {}).get("loaded", len(descendants))),
        "descendants_total": int((descendants_ledger or {}).get("total", len(descendants))),
        "descendants_supported": descendants_supported,
        "descendants_complete": bool((descendants_ledger or {}).get("complete", False)),
        "partial_reasons": partial_reasons,
    }
    if isinstance((descendants_ledger or {}).get("partial_reasons"), list):
        ledger["partial_reasons"].extend([str(r) for r in (descendants_ledger.get("partial_reasons") or []) if r])
    if include_children and not descendants_supported:
        ledger["partial_reasons"].append("descendants_not_supported")

    ledger["source_complete_definition"] = (
        "source_complete requires page_body_complete, comments_complete, attachments_complete, "
        "children_complete, and descendants coverage support."
    )
    ledger["source_metadata_complete"] = bool(ledger["page_body_complete"])
    ledger["source_text_complete"] = bool(ledger["page_body_complete"] and ledger["comments_complete"])
    ledger["source_tree_complete"] = bool(ledger["children_complete"] and ledger["descendants_supported"] and ledger["descendants_complete"])
    ledger["source_complete_for_generation"] = bool(
        ledger["source_metadata_complete"]
        and ledger["source_text_complete"]
        and ledger["attachments_complete"]
        and ledger["children_complete"]
    )
    ledger["source_complete_including_binary_bodies"] = ledger["source_complete_for_generation"]
    ledger["source_complete"] = (
        not partial_reasons
        and ledger["page_body_complete"]
        and ledger["comments_complete"]
        and ledger["attachments_complete"]
        and ledger["children_complete"]
        and ledger["descendants_supported"]
        and ledger["descendants_complete"]
    )

    adapter = ConfluenceFormatAdapter(instance_channel)
    artifact_refs = []
    if include_attachments and attachments:
        base_url = instance_channel.base_url.rstrip("/")
        auth_header = instance_channel._auth_header if instance_channel.is_configured() else None
        for att in attachments:
            filename = str(att.get("title") or "unknown")
            media_type = _infer_attachment_media_type(att, filename)
            is_image = media_type.startswith("image/")
            if is_image or not can_project_to_text(media_type, filename):
                continue
            link = (att.get("_links") or {}).get("download")
            if not link:
                continue
            if attachment_body_policy != "source_complete":
                continue
            try:
                result = await downloader(
                    url=f"{base_url}{link}",
                    session_id=session_id,
                    options={"include_image_data": False, "prefer_text_for_images": False, "vision_enabled": False},
                    auth_header=auth_header,
                    source_type="confluence",
                    source_kind="page_attachment",
                    source_locator=f"{page_id}:{att.get('id')}",
                    provider_metadata={"page_id": page_id, "attachment_id": att.get("id")},
                    persist_text_ref_session_id=session_id,
                    persist_text_ref_kind="confluence_attachment_text",
                    persist_text_ref_source_id=f"{page_id}:{att.get('id')}",
                    persist_text_ref_title=f"Confluence attachment text {filename}",
                    persist_text_ref_metadata={"page_id": page_id, "filename": filename, "attachment_id": att.get("id")},
                )
                if getattr(result, "artifact_id", None):
                    from src.file_artifacts.storage import storage as artifact_storage

                    record = artifact_storage.get_artifact(result.artifact_id)
                    if record:
                        bind_artifact_to_source_bundle(record.artifact_id, f"confluence:{page_id}")
                        artifact_refs.append(build_artifact_ref_dict(record))
                        att["text_preview"] = (result.preview or result.content or "")[:1000]
                        att["text_ref"] = getattr(result, "text_ref", None) or record.text_ref
            except Exception as att_exc:
                logger.warning("Failed attachment materialization for %s: %s", filename, att_exc)

    bundle = {
        "metadata": {
            "page_id": page_id,
            "title": (page or {}).get("title"),
            "space": ((page or {}).get("space") or {}).get("key") if isinstance((page or {}).get("space"), dict) else None,
        },
        "content_markdown": await adapter._to_markdown(page if isinstance(page, dict) else {}),
        "comments": comments,
        "attachments": attachments,
        "artifact_refs": artifact_refs,
        "children": children,
        "descendants": descendants,
        "raw_snapshot": page if include_raw_snapshot else {},
        "completeness_ledger": ledger,
    }

    descendants_pages_complete = True
    descendants_comments_complete = True
    descendants_attachments_complete = True
    if descendants:
        descendants_enriched = []
        for entry in descendants:
            desc_id = str((entry or {}).get("id") or "").strip()
            if not desc_id:
                descendants_pages_complete = False
                continue
            try:
                desc_page = await instance_channel.get_page(desc_id)
                desc_comments, desc_comments_ledger = await instance_channel.get_all_comments_with_ledger(desc_id)
                desc_attachments, desc_attachments_ledger = await instance_channel.get_all_attachments_with_ledger(desc_id)
                desc_markdown = await adapter._to_markdown(desc_page if isinstance(desc_page, dict) else {})
                desc_page_complete = bool(desc_page) and bool(str(desc_markdown or "").strip())
                desc_comments_complete = bool((desc_comments_ledger or {}).get("complete", False))
                desc_attachments_complete = bool((desc_attachments_ledger or {}).get("complete", False))
                descendants_pages_complete = descendants_pages_complete and desc_page_complete
                descendants_comments_complete = descendants_comments_complete and desc_comments_complete
                descendants_attachments_complete = descendants_attachments_complete and desc_attachments_complete
                descendants_enriched.append(
                    {
                        "id": desc_id,
                        "title": (entry or {}).get("title"),
                        "parent_id": (entry or {}).get("parent_id"),
                        "depth": (entry or {}).get("depth"),
                        "space": ((desc_page or {}).get("space") or {}).get("key") if isinstance((desc_page or {}).get("space"), dict) else None,
                        "version": ((desc_page or {}).get("version") or {}).get("number") if isinstance((desc_page or {}).get("version"), dict) else None,
                        "content_markdown": desc_markdown,
                        "descendant_page_body_complete": desc_page_complete,
                        "comments_loaded": int((desc_comments_ledger or {}).get("loaded", len(desc_comments or []))),
                        "comments_total": int((desc_comments_ledger or {}).get("total", len(desc_comments or []))),
                        "comments_complete": desc_comments_complete,
                        "descendant_comments_complete": desc_comments_complete,
                        "attachments_loaded": int((desc_attachments_ledger or {}).get("loaded", len(desc_attachments or []))),
                        "attachments_total": int((desc_attachments_ledger or {}).get("total", len(desc_attachments or []))),
                        "attachments_complete": desc_attachments_complete,
                        "descendant_attachments_complete": desc_attachments_complete,
                    }
                )
            except Exception as desc_item_exc:
                descendants_pages_complete = False
                descendants_comments_complete = False
                descendants_attachments_complete = False
                ledger["partial_reasons"].append(f"descendant_enrich_failed:{desc_id}:{type(desc_item_exc).__name__}")
        bundle["descendants"] = descendants_enriched

    ledger["artifact_refs_created"] = len(artifact_refs)
    ledger["projectable_attachments_total"] = len(
        [
            a
            for a in attachments
            if can_project_to_text(_infer_attachment_media_type(a, str(a.get("title") or "")), str(a.get("title") or ""))
            and not _is_image_attachment(a, str(a.get("title") or ""))
        ]
    )
    ledger["descendants_pages_complete"] = descendants_pages_complete
    ledger["descendants_comments_complete"] = descendants_comments_complete
    ledger["descendants_attachments_complete"] = descendants_attachments_complete
    ledger["descendants_complete"] = bool(
        ledger.get("descendants_complete", False)
        and descendants_pages_complete
        and descendants_comments_complete
        and descendants_attachments_complete
    )
    ledger["source_tree_complete"] = bool(ledger["children_complete"] and ledger["descendants_complete"])
    ledger["source_complete_for_generation"] = bool(
        ledger["source_metadata_complete"]
        and ledger["source_text_complete"]
        and ledger["attachments_complete"]
        and ledger["source_tree_complete"]
    )
    ledger["source_complete"] = bool(
        not ledger["partial_reasons"]
        and ledger["page_body_complete"]
        and ledger["comments_complete"]
        and ledger["attachments_complete"]
        and ledger["source_tree_complete"]
        and ledger["descendants_supported"]
    )

    if session_id:
        persisted = persist_fn(
            session_id=session_id,
            page_id=page_id,
            bundle=bundle,
        )
    else:
        persisted = {
            "context_ref": None,
            "digest_ref": None,
            "source_complete": ledger["source_complete"],
            "partial_reasons": list(ledger.get("partial_reasons") or []),
        }
        if "session_scope_missing" not in ledger["partial_reasons"]:
            ledger["partial_reasons"].append("session_scope_missing")

    from src.file_artifacts.storage import storage as artifact_storage

    refreshed_artifact_refs = []
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

    manifest = {
        "page_id": page_id,
        "context_ref": persisted.get("context_ref"),
        "digest_ref": persisted.get("digest_ref"),
        "source_complete": ledger["source_complete"],
        "source_complete_for_generation": ledger["source_complete_for_generation"],
        "source_complete_including_binary_bodies": ledger["source_complete_including_binary_bodies"],
        "source_metadata_complete": ledger["source_metadata_complete"],
        "source_text_complete": ledger["source_text_complete"],
        "source_tree_complete": ledger["source_tree_complete"],
        "comments_loaded": f"{ledger['comments_loaded']}/{ledger['comments_total']}",
        "attachments_loaded": f"{ledger['attachments_loaded']}/{ledger['attachments_total']}",
        "children_loaded": f"{ledger['children_loaded']}/{ledger['children_total']}",
        "descendants_loaded": ledger.get("descendants_loaded", 0),
        "descendants_total": ledger.get("descendants_total", 0),
        "descendants_supported": ledger.get("descendants_supported", False),
        "descendants_complete": ledger.get("descendants_complete", False),
        "source_complete_definition": ledger.get("source_complete_definition", ""),
        "descendants_pages_complete": ledger.get("descendants_pages_complete", False),
        "descendants_comments_complete": ledger.get("descendants_comments_complete", False),
        "descendants_attachments_complete": ledger.get("descendants_attachments_complete", False),
        "partial_reasons": ledger["partial_reasons"],
        "sections": ["metadata", "content", "comments", "attachments", "children", "descendants", "raw_snapshot"],
    }

    return {
        "page_id": page_id,
        "bundle": bundle,
        "manifest": manifest,
        "persisted": persisted,
        "artifact_refs": bundle.get("artifact_refs") or [],
    }


def format_confluence_source_manifest(source: dict) -> str:
    manifest = source.get("manifest") or {}
    return "[confluence source bundle prepared]\n" + "\n".join(
        f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}" for k, v in manifest.items()
    )
