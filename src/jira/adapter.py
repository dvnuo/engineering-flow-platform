"""
Jira Format Adapter - Unified interface for Markdown/wiki/raw formats.

Provides:
- Query: get_issue (includes comments; default: Markdown)
- Write: create_issue, update_issue, add_comment (default: Markdown)
"""

import logging
import re
from typing import Any, Dict, List, Optional, Union

from src.config import config

try:
    from src.utils.truncate import truncate
except ImportError:
    from ..utils.truncate import truncate
from .api import JiraChannel
from .converter import converter

logger = logging.getLogger(__name__)

_DEFAULT_ACCEPTANCE_CRITERIA_FIELD_NAMES = [
    "Acceptance Criteria",
    "Acceptance Criterion",
    "Acceptance Criteria (AC)",
    "Acceptance Criteria / AC",
    "验收标准",
    "驗收標準",
    "验收条件",
    "驗收條件",
]

_DEFAULT_ACCEPTANCE_CRITERIA_HEADINGS = [*_DEFAULT_ACCEPTANCE_CRITERIA_FIELD_NAMES, "AC"]


def _normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").lower())


class JiraFormatAdapter:
    """Unified interface for Jira operations with format conversion."""

    def __init__(self, channel: JiraChannel):
        self.channel = channel
        self.converter = converter
        # v2 = Server/DC (wiki), v3 = Cloud (ADF)
        self.deployment = 'cloud' if getattr(channel, 'api_version', '2') == '3' else 'server'

    # ========== Query Operations ==========

    async def get_issue(
            self,
            issue_key: str,
            format: str = "markdown",
            max_chars: int = None,
            max_comments: int = 5,
            include_fields: List[str] = None,
            include_comments: bool = True,
            include_attachment_urls: bool = False,
    ) -> Union[str, dict]:
        """Get Jira issue.

        Args:
            issue_key: Jira issue key (e.g., 'PROJ-123')
            format: Output format - "markdown" (default), "wiki", or "raw"
            max_chars: Optional explicit response shortening. Leave None for full Jira issue content.
            max_comments: Maximum number of comments to include
            include_fields: Fields to include (default: summary, status, description, comments)
            include_comments: Whether to include comments
            include_attachment_urls: Markdown-only flag (default: False). When False,
                attachments render as filenames only. When True, markdown attachment
                entries may include URLs.

        Returns:
            markdown/wiki: str
            raw: dict (full issue JSON)
        """
        # Request names and renderedFields to improve custom field detection and
        # to get rendered HTML for description when needed (Cloud / ADF).
        try:
            issue = await self.channel.get_issue(issue_key, expand=["names", "renderedFields"])
        except TypeError:
            # Some channel implementations may not accept expand as kw; fall back.
            issue = await self.channel.get_issue(issue_key, expand=["names", "renderedFields"])

        if include_comments:
            issue = await self._ensure_comments_loaded(issue_key, issue, max_comments)

        if format == "raw":
            return self._format_raw(issue, include_fields, include_comments, max_comments)

        if format == "wiki":
            return self._to_wiki(issue, max_chars, max_comments, include_fields, include_comments)

        # format == "markdown" (default)
        return self._to_markdown(
            issue,
            max_chars,
            max_comments,
            include_fields,
            include_comments,
            include_attachment_urls=include_attachment_urls,
        )

    def _to_markdown(
            self,
            issue: dict,
            max_chars: int = None,
            max_comments: int = 5,
            include_fields: List[str] = None,
            include_comments: bool = True,
            include_attachment_urls: bool = False,
    ) -> str:
        # ...existing code...
        fields = include_fields or ["summary", "status", "description", "acceptance_criteria", "comments", "attachment"]
        lines = []
        issue_key = issue.get("key", "")
        summary = issue.get("fields", {}).get("summary", "")

        lines.append(f"# {issue_key}: {summary}")

        # Status, Type, Priority, Assignee (metadata)
        fields_dict = issue.get("fields", {})
        if "status" in fields:
            status = fields_dict.get("status", {})
            lines.append(f"**Status:** {status.get('name', 'Unknown')}")
        if "issuetype" in fields:
            issue_type = fields_dict.get("issuetype", {})
            lines.append(f"**Type:** {issue_type.get('name', 'Task')}")
        if "priority" in fields:
            priority = fields_dict.get("priority")
            if priority:
                lines.append(f"**Priority:** {priority.get('name', 'None')}")
        if "assignee" in fields:
            assignee = fields_dict.get("assignee")
            if assignee:
                lines.append(f"**Assignee:** {assignee.get('displayName', 'Unassigned')}")

        # Description
        desc_md = None
        ac_from_desc = ""
        if "description" in fields:
            desc = fields_dict.get("description")
            if desc:
                desc_md = self._convert_description_to_markdown(desc)
                # Extract AC from description
                ac_from_desc = self._extract_acceptance_criteria_from_description(issue)
                # Remove AC from description
                desc_md = self._strip_acceptance_criteria_from_markdown_description(desc_md)
        lines.append("\n## Description")
        lines.append(desc_md if desc_md else "N/A")

        # Acceptance Criteria
        ac_from_custom = self._extract_acceptance_criteria_from_custom_fields(
            issue) if "acceptance_criteria" in fields else ""
        ac_lines = []
        if ac_from_desc:
            ac_lines.append(ac_from_desc.strip())
        if ac_from_custom:
            ac_lines.append(ac_from_custom.strip())
        lines.append("\n## Acceptance Criteria")
        lines.append("\n\n".join(ac_lines) if ac_lines else "N/A")

        # Comments
        lines.append("\n## Comments")
        comments = []
        if include_comments and "comments" in fields:
            comments = self._get_comments_list(issue, max_comments)
        if comments:
            for i, comment in enumerate(comments, 1):
                author = comment.get('author', {})
                author_name = author.get('displayName', 'Unknown') if isinstance(author, dict) else (
                            author or 'Unknown')
                created = comment.get('created', '')[:10] if comment.get('created') else 'N/A'
                body = comment.get("body", {})
                body_md = self._convert_description_to_markdown(body) or "N/A"
                # Output body_md directly, do not match or append image urls
                lines.append(f"### {i}) {author_name} - {created}\n{body_md}")
        else:
            lines.append("N/A")

        # Attachments
        lines.append("\n## Attachments")
        issue_fields = issue.get("fields", {})
        attachment_list = issue_fields.get("attachment", [])
        if attachment_list:
            for att in attachment_list:
                filename = att.get("filename", "unknown")
                if include_attachment_urls:
                    url = att.get("content") or att.get("self") or att.get("url") or ""
                    lines.append(f"- {filename} ({url})")
                else:
                    lines.append(f"- {filename}")
        else:
            lines.append("N/A")

        result = "\n".join(lines)
        if max_chars:
            result = truncate(result, max_chars)
        return result

    def _to_wiki(
            self,
            issue: dict,
            max_chars: int = None,
            max_comments: int = 5,
            include_fields: List[str] = None,
            include_comments: bool = True
    ) -> str:
        """Convert issue to Jira wiki format."""
        fields = include_fields or ["summary", "status", "description", "acceptance_criteria", "comments", "attachment"]
        lines = []
        acceptance_criteria = self._extract_acceptance_criteria(issue) if "acceptance_criteria" in fields else ""

        # Get issue key
        issue_key = issue.get("key", "")

        # Summary with key
        if "summary" in fields:
            summary = issue.get("fields", {}).get("summary", "")
            if issue_key:
                lines.append(f"h1. {issue_key}: {summary}")
            else:
                lines.append(f"h1. {summary}")

        # Status
        if "status" in fields:
            status = issue.get("fields", {}).get("status", {})
            lines.append(f"*Status:* {status.get('name', 'Unknown')}")

        # Description
        if "description" in fields:
            desc = issue.get("fields", {}).get("description")
            if desc:
                desc_md = self._convert_description_to_markdown(desc)
                if acceptance_criteria and not self._extract_acceptance_criteria_from_custom_fields(issue):
                    desc_md = self._strip_acceptance_criteria_from_markdown_description(desc_md)
                desc_wiki = self.converter.markdown_to_wiki(desc_md) if desc_md else ""
                if desc_wiki:
                    lines.append(f"\n{desc_wiki}")

        # Acceptance Criteria
        if acceptance_criteria:
            lines.append(f"\nh2. Acceptance Criteria\n{self.converter.markdown_to_wiki(acceptance_criteria)}")

        # Comments (only if explicitly requested)
        if include_comments and "comments" in fields:
            comments = self._get_comments_list(issue, max_comments)
            if comments:
                lines.append("\n-- Comments --")
                for i, comment in enumerate(comments, 1):
                    author = comment.get("author", {})
                    author_name = author.get("displayName", "Unknown") if isinstance(author, dict) else (
                                author or "Unknown")
                    created = comment.get("created", "")[:10]
                    body = comment.get("body", {})
                    body_wiki = self._convert_description_to_wiki(body)
                    lines.append(f"\n*Comment {i}* - {author_name} ({created})")
                    lines.append(body_wiki)

        result = "\n".join(lines)

        # Apply character limit
        if max_chars:
            result = truncate(result, max_chars)

        return result

    def _format_raw(
            self,
            issue: dict,
            include_fields: List[str] = None,
            include_comments: bool = True,
            max_comments: int = 5
    ) -> dict:
        """Return raw issue dict with filtered fields.

        When include_fields is provided, returns a flattened dict with:
        - key: issue key
        - requested fields from fields (promoted to top-level)
        - comments: list (if requested)

        When include_fields is None, returns the original Jira dict.
        """
        if include_fields:
            filtered = {"key": issue.get("key")}
            fields = issue.get("fields", {})

            for field in include_fields:
                if field in fields:
                    filtered[field] = fields[field]

            if "acceptance_criteria" in include_fields:
                filtered["acceptance_criteria"] = self._extract_acceptance_criteria(issue)

            if include_comments and "comments" in include_fields:
                filtered["comments"] = self._get_comments_list(issue, max_comments)

            return filtered

        return issue

    def _convert_description_to_markdown(self, desc: Any) -> str:
        """Convert description to Markdown."""
        if not desc:
            return ""

        # Check if ADF (Cloud)
        if isinstance(desc, dict) and desc.get("type") == "doc":
            return self.converter.adf_to_markdown(desc)

        # Wiki/string (Server/DC)
        if isinstance(desc, str):
            return self.converter.wiki_to_markdown(desc)

        if isinstance(desc, dict):
            for key in ("value", "name", "displayName", "text"):
                value = desc.get(key)
                if isinstance(value, str) and value.strip():
                    return value

        return str(desc)

    def _convert_description_to_wiki(self, desc: Any) -> str:
        """Convert description to wiki."""
        if not desc:
            return ""

        # ADF - convert to wiki first
        if isinstance(desc, dict) and desc.get("type") == "doc":
            md = self.converter.adf_to_markdown(desc)
            return self.converter.markdown_to_wiki(md)

        # Already wiki
        if isinstance(desc, str):
            return desc

        return str(desc)

    def _get_comments_list(self, issue: dict, max_comments: int = 5) -> List[dict]:
        """Extract comments from issue."""
        fields = issue.get("fields", {})
        comments = fields.get("comment", {})

        if isinstance(comments, dict):
            return comments.get("comments", [])[:max_comments]
        elif isinstance(comments, list):
            return comments[:max_comments]

        return []

    async def _ensure_comments_loaded(self, issue_key: str, issue: dict, max_comments: int) -> dict:
        """Load comments explicitly when the issue payload doesn't contain them."""
        existing_comments = self._get_comments_list(issue, max_comments)
        comment_field = issue.get("fields", {}).get("comment", {})
        total_comments = 0
        if isinstance(comment_field, dict):
            total_comments = comment_field.get("total") or len(comment_field.get("comments", []))

        if existing_comments and len(existing_comments) >= min(total_comments or len(existing_comments), max_comments):
            return issue

        try:
            fetched_comments = await self.channel.get_comments(issue_key)
        except Exception as exc:
            logger.debug(f"Failed to load comments via dedicated endpoint for {issue_key}: {exc}")
            return issue

        if fetched_comments:
            issue.setdefault("fields", {})["comment"] = {
                "comments": fetched_comments,
                "total": len(fetched_comments),
            }
        return issue

    def _extract_acceptance_criteria(self, issue: dict) -> str:
        """Extract acceptance criteria from custom fields or description."""
        # Try custom fields first (explicit AC custom fields are preferred)
        field_value = self._extract_acceptance_criteria_from_custom_fields(issue)
        if field_value:
            logger.debug("Acceptance Criteria extracted from custom field(s)")
            return field_value

        # Next try to extract from the description
        desc_value = self._extract_acceptance_criteria_from_description(issue)
        if desc_value:
            logger.debug("Acceptance Criteria extracted from description")
            return desc_value

        # As a last resort, try renderedFields.description (HTML) if present
        rendered = issue.get("renderedFields", {}) or {}
        rf_desc = rendered.get("description")
        if rf_desc:
            try:
                # Strip basic HTML tags and attempt to find AC in plain text
                plain = re.sub(r"<[^>]+>", "", rf_desc).strip()
                if plain:
                    # Reuse the same description extraction logic by feeding plain text
                    ac_from_rendered = self._extract_acceptance_criteria_from_description(
                        {"fields": {"description": plain}})
                    if ac_from_rendered:
                        logger.debug("Acceptance Criteria extracted from renderedFields.description")
                        return ac_from_rendered
            except Exception:
                logger.debug("Failed to parse renderedFields.description for Acceptance Criteria", exc_info=True)

        logger.debug("Acceptance Criteria not found in issue %s", issue.get("key"))
        return ""

    def _extract_acceptance_criteria_from_custom_fields(self, issue: dict) -> str:
        fields = issue.get("fields", {})
        names = issue.get("names", {})
        jira_config = getattr(config, "jira", {}) or {}
        configured_field_ids = jira_config.get("acceptance_criteria_field_ids", []) or []
        configured_field_names = jira_config.get("acceptance_criteria_field_names", []) or []
        candidate_names = configured_field_names or _DEFAULT_ACCEPTANCE_CRITERIA_FIELD_NAMES
        normalized_candidate_names = {_normalize_field_name(name) for name in candidate_names}

        values = []
        seen_field_ids = set()

        for field_id in configured_field_ids:
            if field_id in fields:
                rendered = self._render_field_value(fields.get(field_id))
                if rendered:
                    values.append(rendered)
                    seen_field_ids.add(field_id)

        for field_id, value in fields.items():
            if field_id in seen_field_ids:
                continue
            field_name = names.get(field_id, "")
            if field_name and _normalize_field_name(field_name) in normalized_candidate_names:
                rendered = self._render_field_value(value)
                if rendered:
                    values.append(rendered)
                    seen_field_ids.add(field_id)

        return "\n\n".join(v for v in values if v).strip()

    def _extract_acceptance_criteria_from_description(self, issue: dict) -> str:
        description = issue.get("fields", {}).get("description")
        description_md = self._convert_description_to_markdown(description).strip()
        if not description_md:
            return ""

        heading_names = (getattr(config, "jira", {}) or {}).get("acceptance_criteria_heading_names",
                                                                []) or _DEFAULT_ACCEPTANCE_CRITERIA_HEADINGS
        normalized_headings = {_normalize_field_name(name) for name in heading_names}
        lines = description_md.splitlines()
        collecting = False
        current_level = None
        collected: List[str] = []

        for line in lines:
            stripped = line.strip()
            heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if heading_match:
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip().rstrip(":：")
                normalized_heading = _normalize_field_name(heading_text)
                if normalized_heading in normalized_headings:
                    collecting = True
                    current_level = level
                    continue
                if collecting and current_level is not None and level <= current_level:
                    break
            elif not collecting:
                inline_match = re.match(
                    r"^(acceptance criteria|acceptance criterion|ac|验收标准|驗收標準)\s*[:：-]\s*(.+)$", stripped,
                    re.IGNORECASE)
                if inline_match:
                    collected.append(inline_match.group(2).strip())
                    collecting = True
                    current_level = 6
                    continue

            if collecting:
                collected.append(line)

        return "\n".join(collected).strip()

    def _strip_acceptance_criteria_from_markdown_description(self, description_md: str) -> str:
        """Remove the acceptance-criteria section from a Markdown description block."""
        if not description_md:
            return ""

        heading_names = (getattr(config, "jira", {}) or {}).get("acceptance_criteria_heading_names",
                                                                []) or _DEFAULT_ACCEPTANCE_CRITERIA_HEADINGS
        normalized_headings = {_normalize_field_name(name) for name in heading_names}
        lines = description_md.splitlines()
        result_lines: List[str] = []
        skipping = False
        current_level = None

        for line in lines:
            stripped = line.strip()
            heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if heading_match:
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip().rstrip(":：")
                normalized_heading = _normalize_field_name(heading_text)
                if normalized_heading in normalized_headings:
                    skipping = True
                    current_level = level
                    continue
                if skipping and current_level is not None and level <= current_level:
                    skipping = False
                    current_level = None
            elif skipping and re.match(
                    r"^(acceptance criteria|acceptance criterion|ac|验收标准|驗收標準)\s*[:：-]\s*(.+)$", stripped,
                    re.IGNORECASE):
                continue

            if not skipping:
                result_lines.append(line)

        return "\n".join(result_lines).strip()

    def _render_field_value(self, value: Any) -> str:
        """Render a Jira field value to readable Markdown text."""
        if value is None:
            return ""

        if isinstance(value, (str, dict)):
            return self._convert_description_to_markdown(value).strip()

        if isinstance(value, list):
            rendered_items = [self._render_field_value(item) for item in value]
            rendered_items = [item for item in rendered_items if item]
            if not rendered_items:
                return ""
            if len(rendered_items) == 1:
                return rendered_items[0]
            return "\n".join(f"- {item}" for item in rendered_items)

        return str(value).strip()

    # ========== Write Operations ==========
    
    async def create_issue(
        self,
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
            description: Issue description
            description_format: Input format - "markdown" (default), "wiki", or "raw"
            issue_type: Issue type (Task, Bug, Story, etc.)
            priority: Priority name
            assignee: Assignee account ID or email
            labels: List of labels
            
        Returns:
            Success message with issue key and URL
        """
        # Convert description if needed
        converted_desc = self._convert_description(description, description_format)
        
        result = await self.channel.create_issue(
            project=project_key,
            summary=summary,
            description=converted_desc,
            issue_type=issue_type,
            priority=priority,
            assignee=assignee,
            labels=labels
        )
        
        if isinstance(result, dict):
            issue_key = result.get("key", "")
            url = result.get("self", "")
            # Extract base URL
            if url:
                url = url.split("/rest/")[0] + f"/browse/{issue_key}"
            return f"Issue created: [{issue_key}]({url})"
        
        return str(result)
    
    async def add_comment(
        self,
        issue_key: str,
        body: str,
        body_format: str = "markdown"
    ) -> str:
        """Add a comment to a Jira issue.
        
        Args:
            issue_key: Jira issue key
            body: Comment body
            body_format: Input format - "markdown" (default), "wiki", or "raw"
            
        Returns:
            Success message
        """
        # Convert body if needed
        converted_body = self._convert_description(body, body_format)
        
        result = await self.channel.add_comment(issue_key, converted_body)
        
        if result:
            return f"Comment added to {issue_key}"
        
        return f"Error adding comment to {issue_key}"
    
    async def update_issue(
        self,
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
        # Convert description if provided
        converted_desc = None
        if description is not None:
            converted_desc = self._convert_description(description, description_format)
        
        result = await self.channel.update_issue(
            issue_key=issue_key,
            summary=summary,
            description=converted_desc,
            priority=priority,
            labels=labels
        )
        
        if result:
            return f"Issue {issue_key} updated successfully"
        
        return f"Error updating issue {issue_key}"
    
    def _convert_description(self, text: str, format: str) -> Union[str, Dict[str, Any]]:
        """Convert description/comment body to appropriate format.
        
        Args:
            text: Input text
            format: Input format - "markdown", "wiki", or "raw"
            
        Returns:
            - For wiki (Server/DC): wiki markup string
            - For Cloud: ADF dict (not JSON string)
        """
        if format == "markdown":
            if self.deployment == "cloud":
                # Cloud: return ADF dict directly (channel methods should handle dict)
                return self.converter.markdown_to_adf(text)
            else:
                # Server/DC: convert to wiki
                return self.converter.markdown_to_wiki(text)
        
        # format == "wiki" or "raw" - return as-is
        return text
