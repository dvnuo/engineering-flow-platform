"""Confluence Integration - Single source of truth for Confluence operations."""

import logging
import json
from typing import Optional

from src.utils.attachment import download_and_process_attachment

from .api import (
    ConfluenceChannel, 
    confluence_channel,
)
from .adapter import ConfluenceFormatAdapter, _extract_page_id_from_url
from src.source_context import persist_confluence_source_bundle_and_digest
from src.context_blob_store import put_text

logger = logging.getLogger(__name__)

__all__ = [
    "ConfluenceChannel", 
    "confluence_channel",
    "ConfluenceFormatAdapter",
    "confluence_get_page",
    "confluence_search",
    "confluence_get_page_by_url",
    "confluence_get_page_preview",
    "confluence_get_page_by_url_preview",
    "confluence_create_page",
    "confluence_update_page",
    "confluence_get_comments",
    "confluence_add_comment",
    "confluence_list_spaces",
    "confluence_delete_page",
    "confluence_get_page_history",
    "confluence_get_page_children",
    "confluence_get_space",
    "confluence_list_pages",
    "confluence_get_user",
    "confluence_watch_page",
    "confluence_unwatch_page",
    "confluence_search_by_title",
    "confluence_prepare_page_context",
]


# Factory for creating adapter instances bound to the current channel.


def _get_adapter() -> ConfluenceFormatAdapter:
    """Create a new format adapter bound to the current channel.
    
    A new adapter is returned on each call to avoid caching a channel instance
    in a module-level singleton, which can interfere with tests or runtime reconfiguration.
    """
    return ConfluenceFormatAdapter(confluence_channel)


# ========== Tool Functions ==========


def _infer_attachment_media_type(att: dict, filename: str) -> str:
    """Infer attachment media type from metadata and filename."""
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
    """Check whether an attachment is an image."""
    media_type = _infer_attachment_media_type(att, filename)
    return media_type.startswith("image/")


async def _process_confluence_attachments(
    page_id: str,
    channel: Optional[ConfluenceChannel] = None,
) -> str:
    """Process page attachments and return for LLM."""
    channel = channel or confluence_channel
    try:
        attachments = await channel.get_attachments(page_id)
    except Exception as e:
        logger.debug(f"No attachments for page {page_id}: {e}")
        return ""

    if not attachments:
        return ""

    logger.info(f"Processing {len(attachments)} attachments for page {page_id}")

    shown_attachments = attachments[:5]
    omitted_count = max(len(attachments) - len(shown_attachments), 0)

    results = []
    for att in shown_attachments:
        filename = att.get("title", "unknown")
        size = att.get("extensions", {}).get("fileSize", 0)
        link = att.get("_links", {}).get("download", "")
        media_type = _infer_attachment_media_type(att, filename)

        if _is_image_attachment(att, filename):
            if size:
                results.append(
                    f"- **{filename}** (image, {size} bytes) [image attachment not auto-expanded]"
                )
            else:
                results.append(f"- **{filename}** (image) [image attachment not auto-expanded]")
            continue

        if link:
            base_url = channel.base_url.rstrip("/")
            auth_header = channel._auth_header if channel.is_configured() else None
            download_url = f"{base_url}{link}"

            try:
                result = await download_and_process_attachment(
                    url=download_url,
                    session_id=f"confluence-{page_id}",
                    options={
                        "include_image_data": True,
                        "prefer_text_for_images": True,
                        "vision_enabled": False,
                    },
                    auth_header=auth_header,
                )

                if result.content_format == "text" and result.content:
                    preview = result.content[:500]
                    if size:
                        results.append(f"- **{filename}** ({media_type}, {size} bytes)")
                    else:
                        results.append(f"- **{filename}** ({media_type})")
                    results.append(f"  {preview}")
                elif result.content_format == "base64":
                    if size:
                        results.append(
                            f"- **{filename}** ({media_type}, {size} bytes) [binary attachment omitted]"
                        )
                    else:
                        results.append(f"- **{filename}** ({media_type}) [binary attachment omitted]")
                else:
                    if size:
                        results.append(f"- **{filename}** ({media_type}, {size} bytes)")
                    else:
                        results.append(f"- **{filename}** ({media_type})")
            except Exception as e:
                logger.warning(f"Failed to process {filename}: {e}")
                if size:
                    results.append(f"- **{filename}** ({media_type}, {size} bytes) - [processing failed]")
                else:
                    results.append(f"- **{filename}** ({media_type}) - [processing failed]")
        else:
            if size:
                results.append(f"- **{filename}** ({media_type}, {size} bytes)")
            else:
                results.append(f"- **{filename}** ({media_type})")

    if results:
        header = f"**Attachments:** (showing first {len(shown_attachments)} of {len(attachments)})"
        if omitted_count > 0:
            results.append(f"- ... and {omitted_count} more attachment(s) omitted")
        return header + "\n" + "\n".join(results) + "\n"

    return ""


async def _render_page_with_attachments(
    page_id: str,
    *,
    channel: ConfluenceChannel,
    format: str = "markdown",
    max_chars: Optional[int] = None,
) -> str:
    """Render page content plus processed attachments using a specific channel."""
    adapter = ConfluenceFormatAdapter(channel)
    page_content = await adapter.get_page(page_id, format=format, max_chars=max_chars)
    attachment_info = await _process_confluence_attachments(page_id, channel=channel)
    return page_content + ("\n" + attachment_info if attachment_info else "")

async def confluence_get_page(
    page_id: str,
    format: str = "markdown",
    max_chars: Optional[int] = None,
    preview: bool = False,
    _session_id: Optional[str] = None,
) -> str:
    """Get a Confluence page by ID.
    
    Args:
        page_id: Page ID
        format: "markdown" (default) or "storage"
        max_chars: Optional explicit response shortening for programmatic callers.
            This option is intentionally not exposed in LLM tool schemas; runtime
            context projection controls model-facing size.
    """
    try:
        if not preview:
            return await confluence_prepare_page_context(page_id_or_url=page_id, _session_id=_session_id, include_children=True)
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        
        return await _render_page_with_attachments(
            page_id,
            channel=confluence_channel,
            format=format,
            max_chars=max_chars,
        )
    except Exception as e:
        return f"Error getting page: {e}"


async def confluence_search(query: str, max_results: int = 10) -> str:
    """Search Confluence pages.
    
    Returns title + url + excerpt (plain text).
    
    Args:
        query: CQL search query
        max_results: Maximum results (default 10)
    """
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        
        adapter = _get_adapter()
        return await adapter.search(query, limit=max_results)
    except Exception as e:
        return f"Error searching: {e}"


async def confluence_get_page_by_url(
    url: str,
    format: str = "markdown",
    max_chars: Optional[int] = None,
    preview: bool = False,
    _session_id: Optional[str] = None,
) -> str:
    """Get a Confluence page by its URL.
    
    Uses the URL to find the correct Confluence instance automatically.
    
    Args:
        url: Full Confluence page URL
        format: "markdown" (default) or "storage"
        max_chars: Optional explicit response shortening for programmatic callers.
            This option is intentionally not exposed in LLM tool schemas; runtime
            context projection controls model-facing size.
    """
    try:
        if not preview:
            return await confluence_prepare_page_context(page_id_or_url=url, _session_id=_session_id, include_children=True)
        page_id = _extract_page_id_from_url(url)
        if not page_id:
            return f"Could not extract page ID from URL: {url}"

        instance_channel = confluence_channel.get_instance_client(url=url, strict=True)
        if instance_channel is None:
            return f"Confluence instance for URL is not configured: {url}"

        if not instance_channel.is_configured():
            return f"Confluence instance for URL is not configured: {url}"

        return await _render_page_with_attachments(
            page_id,
            channel=instance_channel,
            format=format,
            max_chars=max_chars,
        )
    except Exception as e:
        return f"Error getting page: {e}"


async def confluence_get_page_preview(*args, **kwargs) -> str:
    kwargs["preview"] = True
    return await confluence_get_page(*args, **kwargs)


async def confluence_get_page_by_url_preview(*args, **kwargs) -> str:
    kwargs["preview"] = True
    return await confluence_get_page_by_url(*args, **kwargs)


async def confluence_prepare_page_context(
    page_id_or_url: str,
    include_comments: bool = True,
    include_attachments: bool = True,
    include_children: bool = True,
    include_raw_snapshot: bool = True,
    _session_id: Optional[str] = None,
) -> str:
    """Prepare source-complete Confluence page context."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."

        target = str(page_id_or_url or "").strip()
        page_id = target
        instance_channel = confluence_channel
        if target.startswith("http://") or target.startswith("https://"):
            extracted = _extract_page_id_from_url(target)
            if not extracted:
                return f"Could not extract page ID from URL: {page_id_or_url}"
            page_id = extracted
            instance_channel = confluence_channel.get_instance_client(url=target) or confluence_channel

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
                descendants_ledger = {"loaded": 0, "total": 0, "complete": False, "partial_reasons": [f"descendants_fetch_failed:{type(desc_exc).__name__}"]}

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
        bundle = {
            "metadata": {
                "page_id": page_id,
                "title": (page or {}).get("title"),
                "space": ((page or {}).get("space") or {}).get("key") if isinstance((page or {}).get("space"), dict) else None,
            },
            "content_markdown": await adapter._to_markdown(page if isinstance(page, dict) else {}),
            "comments": comments,
            "attachments": attachments,
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
        persisted = persist_confluence_source_bundle_and_digest(
            session_id=_session_id or "unknown_session",
            page_id=page_id,
            bundle=bundle,
        )
        manifest = {
            "page_id": page_id,
            "context_ref": persisted["context_ref"],
            "digest_ref": persisted["digest_ref"],
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
        return "[confluence source bundle prepared]\n" + "\n".join(
            f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}" for k, v in manifest.items()
        )
    except Exception as e:
        return f"Error preparing confluence source context: {e}"


async def confluence_create_page(
    space_key: str,
    title: str,
    body: str = "",
    parent_id: str = None,
    body_format: str = "markdown"
) -> str:
    """Create a new Confluence page.
    
    Args:
        space_key: Space key (e.g., 'DEV')
        title: Page title
        body: Page content
        parent_id: Parent page ID (optional)
        body_format: "markdown" (default) or "storage"
    """
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        
        adapter = _get_adapter()
        return await adapter.create_page(space_key, title, body, body_format=body_format, parent_id=parent_id)
    except Exception as e:
        return f"Error creating page: {e}"


async def confluence_update_page(
    page_id: str,
    title: str = None,
    body: str = None,
    body_format: str = "markdown"
) -> str:
    """Update an existing Confluence page.
    
    Args:
        page_id: Page ID
        title: New title (optional, fetches current if not provided)
        body: New content (optional)
        body_format: "markdown" (default) or "storage"
    """
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        
        adapter = _get_adapter()
        return await adapter.update_page(page_id, title=title, body=body, body_format=body_format)
    except Exception as e:
        return f"Error updating page: {e}"


async def confluence_get_comments(page_id: str, _session_id: Optional[str] = None) -> str:
    """Get source-complete Confluence comments with ledger and context refs."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        comments, ledger = await confluence_channel.get_all_comments_with_ledger(page_id)
        comments = comments or []
        ledger = ledger or {}
        context_ref = put_text(
            session_id=_session_id or "unknown_session",
            kind="confluence_comments_bundle",
            source_id=page_id,
            title=f"Confluence comments {page_id}",
            content=json.dumps({"page_id": page_id, "comments": comments}, ensure_ascii=False, indent=2),
            metadata={"page_id": page_id, "comments_complete": bool(ledger.get("complete", False))},
        )
        return (
            "[confluence comments prepared]\n"
            f"page_id: {page_id}\n"
            f"context_ref: {context_ref}\n"
            f"comments_loaded: {int(ledger.get('loaded', len(comments)))}/{int(ledger.get('total', len(comments)))}\n"
            f"comments_complete: {bool(ledger.get('complete', False))}\n"
            "comments_preview: omitted (use context_read_ref for full comments)"
        )
    except Exception as e:
        return f"Error getting comments: {e}"


async def confluence_add_comment(page_id: str, comment: str) -> str:
    """Add a comment to a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        await confluence_channel.add_comment(page_id, comment)
        return f"Comment added to page {page_id}"
    except Exception as e:
        return f"Error adding comment: {e}"


async def confluence_list_spaces(limit: int = 20) -> str:
    """List all Confluence spaces."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.list_spaces(limit)
        
        if isinstance(result, dict):
            spaces = result.get("results", [])
            if not spaces:
                return "No spaces found."
            
            lines = [f"**Spaces** ({len(spaces)}):\n"]
            for s in spaces:
                if not isinstance(s, dict):
                    continue
                name = s.get("name", "Untitled")
                key = s.get("key", "?")
                lines.append(f"- **{name}** ({key})")
            return "\n".join(lines)
        return str(result)
    except Exception as e:
        return f"Error listing spaces: {e}"


async def confluence_delete_page(page_id: str) -> str:
    """Delete a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        await confluence_channel.delete_page(page_id)
        return f"Page {page_id} deleted successfully"
    except Exception as e:
        return f"Error deleting page: {e}"


async def confluence_get_page_history(page_id: str) -> str:
    """Get version history of a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_page_history(page_id)
        
        if isinstance(result, dict):
            versions = result.get("results", [])
            if not versions:
                return "No history found."
            
            lines = [f"**Version History** ({len(versions)} versions):\n"]
            for v in versions:
                if not isinstance(v, dict):
                    continue
                number = v.get("number", "?")
                when = v.get("createdAt", "")[:10]
                author = v.get("author", {}).get("displayName", "Unknown") if isinstance(v.get("author"), dict) else "Unknown"
                lines.append(f"- v{number} by {author} on {when}")
            return "\n".join(lines)
        return str(result)
    except Exception as e:
        return f"Error getting page history: {e}"


async def confluence_get_page_children(page_id: str, limit: int = 10, _session_id: Optional[str] = None) -> str:
    """Get child pages of a Confluence page with ledger completeness."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        children, ledger = await confluence_channel.get_all_page_children_with_ledger(page_id, limit=max(limit, 100))
        children = children or []
        ledger = ledger or {}
        context_ref = put_text(
            session_id=_session_id or f"confluence-children-{page_id}",
            kind="confluence_children_bundle",
            source_id=page_id,
            title=f"Confluence children {page_id}",
            content=json.dumps({"page_id": page_id, "children": children}, ensure_ascii=False, indent=2),
            metadata={"page_id": page_id, "children_complete": bool(ledger.get("complete", False))},
        )
        lines = [f"[confluence children prepared]\npage_id: {page_id}\ncontext_ref: {context_ref}"]
        lines.append(f"children_loaded: {int(ledger.get('loaded', len(children)))}/{int(ledger.get('total', len(children)))}")
        lines.append(f"children_complete: {bool(ledger.get('complete', False))}")
        lines.append("children_preview: omitted (use context_read_ref for full child list)")
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting page children: {e}"


async def confluence_get_space(space_key: str) -> str:
    """Get details of a Confluence space."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_space(space_key)
        
        if isinstance(result, dict):
            name = result.get("name", "Untitled")
            description = result.get("description", {}).get("plain", {}).get("value", "No description")
            return f"**Space: {name}** ({space_key})\n\n{description}"
        return str(result)
    except Exception as e:
        return f"Error getting space: {e}"


async def confluence_list_pages(space_key: str, limit: int = 20) -> str:
    """List all pages in a Confluence space."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.list_pages(space_key, limit)
        
        if isinstance(result, dict):
            pages = result.get("results", [])
            if not pages:
                return "No pages found in this space."
            
            lines = [f"**Pages in {space_key}** ({len(pages)}):\n"]
            for p in pages:
                if not isinstance(p, dict):
                    continue
                title = p.get("title", "Untitled")
                page_id = p.get("id", "?")
                lines.append(f"- **{title}** ({page_id})")
            return "\n".join(lines)
        return str(result)
    except Exception as e:
        return f"Error listing pages: {e}"


async def confluence_get_user(user_id: str = None, username: str = None) -> str:
    """Get user details from Confluence."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_user(user_id, username)
        
        if isinstance(result, dict):
            display_name = result.get("displayName", "Unknown")
            email = result.get("publicName", "No email")
            return f"**User: {display_name}**\nEmail: {email}"
        return str(result)
    except Exception as e:
        return f"Error getting user: {e}"


async def confluence_watch_page(page_id: str) -> str:
    """Watch a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        await confluence_channel.watch_page(page_id)
        return f"Now watching page {page_id}"
    except Exception as e:
        return f"Error watching page: {e}"


async def confluence_unwatch_page(page_id: str) -> str:
    """Unwatch a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        await confluence_channel.unwatch_page(page_id)
        return f"Stopped watching page {page_id}"
    except Exception as e:
        return f"Error unwatching page: {e}"


async def confluence_search_by_title(title: str, space_key: str = None) -> str:
    """Search Confluence pages by title."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_page_by_title(space_key, title)
        
        if result and isinstance(result, dict):
            page_id = result.get("id", "?")
            page_title = result.get("title", title)
            url = result.get("url", "")
            return f"**{page_title}**\nID: {page_id}\nURL: {url}"
        return f"No page found with title: {title}"
    except Exception as e:
        return f"Error searching by title: {e}"


def get_tools_schemas() -> list:
    """Return Confluence tool schemas for OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page",
                "description": "Get a Confluence page by ID. Default model-facing behavior prepares source-complete context manifest.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Confluence page ID (numeric)"},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "storage"],
                            "default": "markdown",
                            "description": "Output format: markdown (default) or storage"
                        },
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page_by_url",
                "description": "Get a Confluence page by URL. Default model-facing behavior prepares source-complete context manifest.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full Confluence page URL"},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "storage"],
                            "default": "markdown",
                            "description": "Output format: markdown (default) or storage"
                        },
                    },
                    "required": ["url"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_prepare_page_context",
                "description": "Prepare source-complete Confluence page context (body + comments + attachments + children) for generation workflows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id_or_url": {"type": "string", "description": "Confluence page ID or full URL"},
                        "include_comments": {"type": "boolean", "default": True},
                        "include_attachments": {"type": "boolean", "default": True},
                        "include_children": {"type": "boolean", "default": True},
                        "include_raw_snapshot": {"type": "boolean", "default": True}
                    },
                    "required": ["page_id_or_url"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_search",
                "description": "Discovery tool: search Confluence pages using CQL and return lightweight preview metadata.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "CQL search query"},
                        "max_results": {"type": "integer", "description": "Maximum results to return", "default": 10}
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_search_by_title",
                "description": "Discovery tool: search Confluence pages by exact title and return lightweight preview metadata",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Page title to search for"},
                        "space_key": {"type": "string", "description": "Optional space key to limit search", }
                    },
                    "required": ["title"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_create_page",
                "description": "Create a new Confluence page. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "space_key": {"type": "string", "description": "Space key (e.g., 'DEV', 'TEAM')"},
                        "title": {"type": "string", "description": "Page title"},
                        "body": {"type": "string", "description": "Page content (Markdown or HTML)", "default": ""},
                        "body_format": {
                            "type": "string",
                            "enum": ["markdown", "storage"],
                            "default": "markdown",
                            "description": "Input format: markdown (default) or storage"
                        },
                        "parent_id": {"type": "string", "description": "Parent page ID for hierarchy", }
                    },
                    "required": ["space_key", "title"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_update_page",
                "description": "Update an existing Confluence page. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID to update"},
                        "title": {"type": "string", "description": "New title (optional)"},
                        "body": {"type": "string", "description": "New content (Markdown or HTML, optional)"},
                        "body_format": {
                            "type": "string",
                            "enum": ["markdown", "storage"],
                            "default": "markdown",
                            "description": "Input format: markdown (default) or storage"
                        }
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_delete_page",
                "description": "Delete a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID to delete"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_comments",
                "description": "Get all comments on a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_add_comment",
                "description": "Add a comment to a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID"},
                        "comment": {"type": "string", "description": "Comment text"}
                    },
                    "required": ["page_id", "comment"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_list_spaces",
                "description": "List all Confluence spaces",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Maximum number of spaces to return", "default": 20}
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_space",
                "description": "Get details of a Confluence space",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "space_key": {"type": "string", "description": "Space key (e.g., 'DEV', 'TEAM')"}
                    },
                    "required": ["space_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_list_pages",
                "description": "Discovery tool: list pages in a Confluence space (preview metadata only)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "space_key": {"type": "string", "description": "Space key"},
                        "limit": {"type": "integer", "description": "Maximum number of pages", "default": 20}
                    },
                    "required": ["space_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page_children",
                "description": "Prepare bounded child-page manifest with completeness ledger and context_ref",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Parent page ID"},
                        "limit": {"type": "integer", "description": "Maximum number of children", "default": 10}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page_history",
                "description": "Get version history of a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_user",
                "description": "Get user details from Confluence",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "User ID", },
                        "username": {"type": "string", "description": "Username", }
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_watch_page",
                "description": "Watch a Confluence page (get notifications)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID to watch"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_unwatch_page",
                "description": "Stop watching a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID to unwatch"}
                    },
                    "required": ["page_id"]
                }
            }
        },
    ]
