"""Jira Integration - Single source of truth for Jira operations."""

import logging
from typing import List, Optional, Union
import json

logger = logging.getLogger(__name__)

from .api import (
    JiraChannel, 
    jira_channel,
    jira_search,
    jira_add_attachment,
    jira_transition,
    jira_get_transitions,
    jira_assign_issue,
    jira_get_projects,
    jira_get_components,
    jira_get_versions,
    jira_get_worklog,
    jira_add_worklog,
    jira_get_comments,
)
from .adapter import JiraFormatAdapter
from src.utils.attachment import download_and_process_attachment
from .exporter import jira_export_issues_to_markdown
from src.source_context import persist_jira_source_bundle_and_digest
from src.context_blob_store import put_text
from .source_service import prepare_jira_issue_source, format_jira_source_manifest

__all__ = [
    "JiraChannel", 
    "jira_channel",
    "JiraFormatAdapter",
    "jira_get_issue",
    "jira_get_issue_by_url",
    "jira_get_issue_preview",
    "jira_get_issue_by_url_preview",
    "jira_prepare_issue_context",
    "jira_search",
    "jira_add_comment",
    "jira_add_attachment",
    "jira_create_issue",
    "jira_update_issue",
    "jira_transition",
    "jira_get_transitions",
    "jira_get_comments",
    "jira_assign_issue",
    "jira_get_projects",
    "jira_get_components",
    "jira_get_versions",
    "jira_get_worklog",
    "jira_add_worklog",
    "jira_export_issues_to_markdown",
]


def _get_adapter() -> JiraFormatAdapter:
    """Create a new format adapter bound to the current channel."""
    return JiraFormatAdapter(jira_channel)


# ========== Tool Functions with Markdown Support ==========



async def _process_issue_attachments(issue_key: str, fields: dict) -> str:
    """Process issue attachments and return them for LLM."""
    attachments = fields.get("attachment", [])
    if not attachments:
        return ""
    
    logger.info(f"Processing {len(attachments)} attachments for {issue_key}")
    
    results = []
    for i, att in enumerate(attachments[:5]):  # Max 5 attachments
        filename = att.get("filename", "unknown")
        mime_type = att.get("mimeType", "application/octet-stream")
        size = att.get("size", 0)
        
        content_url = att.get("content", "")
        
        if content_url:
            try:
                # Get auth header from Jira channel
                auth_header = jira_channel._auth_header if jira_channel.is_configured() else None
                
                result = await download_and_process_attachment(
                    url=content_url,
                    session_id=f"jira-{issue_key}",
                    options={"include_image_data": True},
                    auth_header=auth_header
                )
                
                if result.content_format == "base64":
                    results.append(f"- **{filename}** (image, {size} bytes)")
                    results.append(f"  {result.content}")
                elif result.content_format == "text" and result.content:
                    preview = result.content[:500]
                    results.append(f"- **{filename}** (text, {size} bytes)")
                    results.append(f"  {preview}")
                else:
                    results.append(f"- **{filename}** ({mime_type}, {size} bytes)")
            except Exception as e:
                logger.warning(f"Failed to process attachment {filename}: {e}")
                results.append(f"- **{filename}** ({mime_type}, {size} bytes) - [processing failed]")
        else:
            results.append(f"- **{filename}** ({mime_type}, {size} bytes)")
    
    if results:
        return "**Attachments:**\n" + "\n".join(results) + "\n"
    return ""

async def jira_get_issue(
    issue_key: str,
    format: str = "markdown",
    max_chars: int = None,
    max_comments: int = 5,
    include_fields: List[str] = None,
    include_comments: bool = True,
    include_attachment_urls: bool = False,
    preview: bool = False,
    _session_id: Optional[str] = None,
) -> Union[str, dict]:
    """Get a Jira issue by key.
    
    Args:
        issue_key: Jira issue key (e.g., 'PROJ-123')
        format: Output format - "markdown" (default), "wiki", or "raw"
        max_chars: Optional explicit response shortening for programmatic callers.
            This option is intentionally not exposed in LLM tool schemas; runtime
            context projection controls model-facing size.
        max_comments: Maximum number of comments to include
        include_fields: Fields to include (default: summary, status, description, comments)
        include_comments: Whether to include comments
        include_attachment_urls: Markdown-only flag (default: False). Controls
            whether attachment rendering includes full URLs. By default,
            attachment filenames are shown without URLs.
        
    Returns:
        Issue details in requested format (markdown/wiki: str, raw: dict)
    """
    try:
        if not preview:
            return await jira_prepare_issue_context(issue_key_or_url=issue_key, _session_id=_session_id)
        if not jira_channel.is_configured():
            return "Error: Jira is not configured. Please check your settings."
        
        adapter = _get_adapter()
        result = await adapter.get_issue(
            issue_key=issue_key,
            format=format,
            max_chars=max_chars,
            max_comments=max_comments,
            include_fields=include_fields,
            include_comments=include_comments,
            include_attachment_urls=include_attachment_urls
        )
        
        # Process attachments - need to fetch raw issue to get attachment field
        attachment_info = ""
        try:
            if format == "raw":
                fields = result.get("fields", {}) if isinstance(result, dict) else {}
                attachment_info = await _process_issue_attachments(issue_key, fields)
            else:
                # For markdown/wiki, fetch attachment metadata separately
                issue_data = await jira_channel.get_issue(issue_key)
                fields = issue_data.get("fields", {}) if isinstance(issue_data, dict) else {}
                attachment_info = await _process_issue_attachments(issue_key, fields)
        except Exception as e:
            logger.warning(f"Failed to process attachments: {e}")
        
        if attachment_info:
            if isinstance(result, str):
                result = result + "\n" + attachment_info
            elif isinstance(result, dict):
                result["attachment_info"] = attachment_info
        
        return result
    except Exception as e:
        return f"Error getting issue {issue_key}: {str(e)}"


async def jira_get_issue_by_url(
    url: str,
    format: str = "markdown",
    max_chars: int = None,
    max_comments: int = 5,
    include_fields: List[str] = None,
    include_comments: bool = True,
    include_attachment_urls: bool = False,
    preview: bool = False,
    _session_id: Optional[str] = None,
) -> Union[str, dict]:
    """Get a Jira issue by its URL.
    
    Args:
        url: Full Jira issue URL (e.g., https://company.atlassian.net/browse/PROJ-123)
        format: Output format - "markdown" (default), "wiki", or "raw"
        max_chars: Optional explicit response shortening for programmatic callers.
            This option is intentionally not exposed in LLM tool schemas; runtime
            context projection controls model-facing size.
        max_comments: Maximum number of comments to include
        include_fields: Fields to include
        include_comments: Whether to include comments
        include_attachment_urls: Markdown-only flag (default: False). Controls
            whether attachment rendering includes full URLs. By default,
            attachment filenames are shown without URLs.
        
    Returns:
        Issue details in requested format (markdown/wiki: str, raw: dict)
    """
    import re
    
    try:
        if not preview:
            return await jira_prepare_issue_context(issue_key_or_url=url, _session_id=_session_id)
        # Extract issue key from URL (support letters, digits, underscores in project key)
        match = re.search(r'/browse/([A-Z][A-Z0-9_]*-\d+)', url, re.IGNORECASE)
        if not match:
            return f"Could not extract issue key from URL: {url}"
        
        issue_key = match.group(1).upper()
        
        # Get the correct instance client based on URL
        if not jira_channel.is_configured():
            return "Error: Jira is not configured."
        
        instance_channel = jira_channel.get_instance_client(url=url)
        
        if not instance_channel.is_configured():
            return f"Error: Jira instance for {url} is not configured."
        
        adapter = JiraFormatAdapter(instance_channel)
        
        result = await adapter.get_issue(
            issue_key=issue_key,
            format=format,
            max_chars=max_chars,
            max_comments=max_comments,
            include_fields=include_fields,
            include_comments=include_comments,
            include_attachment_urls=include_attachment_urls
        )
        
        # Process attachments
        if isinstance(result, dict):
            try:
                fields = result.get("fields", {})
                attachment_info = await _process_issue_attachments(issue_key, fields)
                if attachment_info:
                    # Convert dict to markdown if needed
                    result = str(result) + "\n" + attachment_info
            except Exception as e:
                logger.warning(f"Failed to process attachments: {e}")
        
        return result
    except Exception as e:
        return f"Error getting issue from URL: {str(e)}"


async def jira_get_issue_preview(*args, **kwargs) -> Union[str, dict]:
    kwargs["preview"] = True
    return await jira_get_issue(*args, **kwargs)


async def jira_get_issue_by_url_preview(*args, **kwargs) -> Union[str, dict]:
    kwargs["preview"] = True
    return await jira_get_issue_by_url(*args, **kwargs)


async def jira_prepare_issue_context(
    issue_key_or_url: str,
    include_all_comments: bool = True,
    include_attachments: bool = True,
    include_raw_snapshot: bool = True,
    _session_id: Optional[str] = None,
) -> Union[str, dict]:
    """Prepare a source-complete Jira context bundle and bounded digest manifest."""
    try:
        result = await prepare_jira_issue_source(
            issue_key_or_url=issue_key_or_url,
            include_all_comments=include_all_comments,
            include_attachments=include_attachments,
            include_raw_snapshot=include_raw_snapshot,
            session_id=_session_id or "unknown_session",
            attachment_body_policy="source_complete",
        )
        return format_jira_source_manifest(result)
    except Exception as e:
        return f"Error preparing Jira issue context {issue_key_or_url}: {str(e)}"


async def jira_get_comments(issue_key: str, _session_id: Optional[str] = None) -> str:
    """Prepare bounded Jira comments bundle manifest with context ref."""
    if not jira_channel.is_configured():
        return "Error: Jira is not configured."
    adapter = _get_adapter()
    issue = await adapter.get_issue(
        issue_key=issue_key,
        format="raw",
        max_comments=None,
        include_comments=True,
        include_fields=None,
    )
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    comments = adapter._get_comments_list(issue if isinstance(issue, dict) else {}, None)
    comment_field = fields.get("comment", {}) if isinstance(fields, dict) else {}
    comments_total = int((comment_field or {}).get("total") or len(comments)) if isinstance(comment_field, dict) else len(comments)
    comments_loaded = len(comments)
    comments_complete = comments_loaded >= comments_total
    context_ref = put_text(
        session_id=_session_id or "unknown_session",
        kind="jira_comments_bundle",
        source_id=issue_key,
        title=f"Jira comments {issue_key}",
        content=json.dumps({"issue_key": issue_key, "comments": comments}, ensure_ascii=False, indent=2),
        metadata={"issue_key": issue_key, "comments_complete": comments_complete},
    )
    return (
        "[jira comments bundle prepared]\n"
        f"issue_key: {issue_key}\n"
        f"context_ref: {context_ref}\n"
        f"comments_loaded: {comments_loaded}/{comments_total}\n"
        f"comments_complete: {comments_complete}"
    )


async def jira_add_comment(
    issue_key: str,
    body: str = None,
    body_format: str = "markdown",
    comment: str = None
) -> str:
    """Add a comment to a Jira issue.
    
    Args:
        issue_key: Jira issue key
        body: Comment body (Markdown by default) - alias: comment
        body_format: Input format - "markdown" (default), "wiki", or "raw"
        comment: Alias for body
        
    Returns:
        Success message
    """
    # Support both "body" and "comment" parameter names
    body = body if body is not None else (comment or "")
    try:
        if not jira_channel.is_configured():
            return "Error: Jira is not configured."
        
        adapter = _get_adapter()
        return await adapter.add_comment(issue_key, body, body_format=body_format)
    except Exception as e:
        return f"Error adding comment: {str(e)}"


async def jira_create_issue(
    project_key: str,
    summary: str,
    description: str = "",
    description_format: str = "markdown",
    issue_type: str = "Task",
    priority: str = None,
    assignee: str = None,
    labels: List[str] = None
) -> str:
    """Create a new Jira issue.
    
    Args:
        project_key: Project key (e.g., 'PROJ')
        summary: Issue summary/title
        description: Issue description (Markdown by default)
        description_format: Input format - "markdown" (default), "wiki", or "raw"
        issue_type: Issue type (Task, Bug, Story, etc.)
        priority: Priority name
        assignee: Assignee account ID or email
        labels: List of labels
        
    Returns:
        Success message with issue key and URL
    """
    try:
        if not jira_channel.is_configured():
            return "Error: Jira is not configured."
        
        adapter = _get_adapter()
        return await adapter.create_issue(
            project_key=project_key,
            summary=summary,
            description=description,
            description_format=description_format,
            issue_type=issue_type,
            priority=priority,
            assignee=assignee,
            labels=labels
        )
    except Exception as e:
        return f"Error creating issue: {str(e)}"


async def jira_update_issue(
    issue_key: str,
    summary: str = None,
    description: str = None,
    description_format: str = "markdown",
    priority: str = None,
    labels: List[str] = None
) -> str:
    """Update a Jira issue.
    
    Args:
        issue_key: Jira issue key
        summary: New summary (optional)
        description: New description (optional)
        description_format: Input format - "markdown" (default), "wiki", or "raw"
        priority: New priority (optional)
        labels: New labels (optional)
        
    Returns:
        Success message
    """
    try:
        if not jira_channel.is_configured():
            return "Error: Jira is not configured."
        
        adapter = _get_adapter()
        return await adapter.update_issue(
            issue_key=issue_key,
            summary=summary,
            description=description,
            description_format=description_format,
            priority=priority,
            labels=labels
        )
    except Exception as e:
        return f"Error updating issue: {str(e)}"


from .api import get_tools_schemas as _get_api_schemas


def get_tools_schemas() -> list:
    """Get all Jira tool schemas with Markdown support.
    
    Delegates to api.get_tools_schemas() and augments Markdown-related tools.
    """
    # Get base schemas from api
    base_tools = _get_api_schemas()
    
    # Map of tool names to their enhanced schemas in this module
    enhanced_schemas = {}
    current_schemas = _get_all_schemas()
    for schema in current_schemas:
        name = schema.get("function", {}).get("name", "")
        if name:
            enhanced_schemas[name] = schema
    
    # Replace enhanced tools, keep others from base
    result = []
    seen_names = set()
    for tool in base_tools:
        name = tool.get("function", {}).get("name", "")
        if name:
            seen_names.add(name)
        if name in enhanced_schemas:
            result.append(enhanced_schemas[name])
        else:
            result.append(tool)
    
    # Append any enhanced tools that aren't in base (e.g., jira_get_issue_by_url)
    for name, schema in enhanced_schemas.items():
        if name not in seen_names:
            result.append(schema)

    # Preview tools remain internal. The export tool is model-facing because users
    # explicitly ask for "Jira tickets to markdown / save to folder / download attachments".
    return [
        schema for schema in result
        if schema.get("function", {}).get("name") not in {"jira_get_issue_preview", "jira_get_issue_by_url_preview"}
    ]


def _get_all_schemas() -> list:
    """Return all Jira tool schemas with Markdown support."""
    return [
        {
            "type": "function",
            "function": {
                "name": "jira_get_issue",
                "description": "Get a Jira issue by key. Default model-facing behavior prepares source-complete context manifest.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Output format: markdown (LLM-friendly), wiki (renderable), or raw (JSON)"
                        },
                        "include_fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Fields to include (default: summary, status, description, acceptance_criteria, comments)"
                        },
                        "include_comments": {
                            "type": "boolean",
                            "description": "Whether to include comments",
                            "default": True
                        },
                        "include_attachment_urls": {
                            "type": "boolean",
                            "description": "Whether to include attachment URLs in markdown output",
                            "default": False
                        }
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_issue_by_url",
                "description": "Get a Jira issue by URL. Default model-facing behavior prepares source-complete context manifest.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full Jira issue URL (e.g., https://company.atlassian.net/browse/PROJ-123)"},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Output format: markdown, wiki, or raw"
                        },
                        "include_comments": {"type": "boolean", "description": "Include comments", "default": True},
                        "include_attachment_urls": {
                            "type": "boolean",
                            "description": "Whether to include attachment URLs in markdown output",
                            "default": False
                        },
                        "include_fields": {"type": "array", "items": {"type": "string"}, "description": "Fields to include"}
                    },
                    "required": ["url"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_prepare_issue_context",
                "description": "Prepare a source-complete Jira context bundle and bounded digest for generation workflows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key_or_url": {"type": "string", "description": "Jira issue key (PROJ-123) or full browse URL"},
                        "include_all_comments": {"type": "boolean", "default": True},
                        "include_attachments": {"type": "boolean", "default": True},
                        "include_raw_snapshot": {"type": "boolean", "default": True},
                    },
                    "required": ["issue_key_or_url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "jira_create_issue",
                "description": "Create a new Jira issue. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_key": {"type": "string", "description": "Project key (e.g., PROJ)"},
                        "summary": {"type": "string", "description": "Issue summary/title"},
                        "description": {"type": "string", "description": "Issue description (Markdown by default)"},
                        "description_format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Input format: markdown, wiki, or raw"
                        },
                        "issue_type": {"type": "string", "description": "Issue type", "default": "Task"},
                        "priority": {"type": "string", "description": "Priority name"},
                        "labels": {"type": "array", "items": {"type": "string"}, "description": "List of labels"}
                    },
                    "required": ["project_key", "summary"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_update_issue",
                "description": "Update a Jira issue. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "summary": {"type": "string", "description": "New summary (optional)"},
                        "description": {"type": "string", "description": "New description (optional)"},
                        "description_format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Input format: markdown, wiki, or raw"
                        },
                        "priority": {"type": "string", "description": "New priority"},
                        "labels": {"type": "array", "items": {"type": "string"}, "description": "New labels"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_add_comment",
                "description": "Add a comment to a Jira issue. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-123)"},
                        "comment": {"type": "string", "description": "Comment body (Markdown by default)"},
                        "body_format": {
                            "type": "string",
                            "enum": ["markdown", "wiki", "raw"],
                            "default": "markdown",
                            "description": "Input format: markdown, wiki, or raw"
                        }
                    },
                    "required": ["issue_key", "comment"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_search",
                "description": "Search Jira issues using JQL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "jql": {"type": "string", "description": "JQL query string"},
                        "max_results": {"type": "integer", "description": "Maximum results", "default": 10}
                    },
                    "required": ["jql"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "jira_get_comments",
                "description": "Prepare bounded Jira comments manifest with completeness ledger and context_ref.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key"}
                    },
                    "required": ["issue_key"]
                }
            }
        },
    ]
