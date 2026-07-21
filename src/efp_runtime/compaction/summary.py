"""Anchored compaction summary rendering for EFP runtime."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import re
from typing import Any

from ..session.models import Message, MessagePart, MessagePartType


COMPACTION_SUMMARY_HEADINGS = (
    "## Goal",
    "## Constraints & Preferences",
    "## Progress",
    "### Done",
    "### In Progress",
    "### Blocked",
    "## Key Decisions",
    "## Next Steps",
    "## Critical Context",
    "## Relevant Files",
)

DEFAULT_COMPACTION_PROMPT_CONTEXT_CHAR_LIMIT = 24000
PREVIOUS_SUMMARY_CONTEXT_CHAR_LIMIT = 4000
RELEVANT_FILE_LIMIT = 24

_PATH_PATTERN = re.compile(
    r"(?<![\w./:-])"
    r"("
    r"/[A-Za-z0-9._~+@%=-]+(?:/[A-Za-z0-9._~+@%=-]+)+(?:[:#][0-9A-Za-z._~+@%=-]+)?"
    r"|"
    r"(?:\.{1,2}/)?[A-Za-z0-9._~+@%=-]+(?:/[A-Za-z0-9._~+@%=-]+)+"
    r"(?:[:#][0-9A-Za-z._~+@%=-]+)?"
    r")"
)


def build_compaction_prompt(
    *,
    session_id: str,
    messages: Iterable[Message],
    compacted_messages: Iterable[Message],
    kept_messages: Iterable[Message],
    previous_summary: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    context_char_limit: int = DEFAULT_COMPACTION_PROMPT_CONTEXT_CHAR_LIMIT,
) -> tuple[str, dict[str, Any]]:
    """Build a bounded structured prompt for a custom compaction summarizer."""

    source_messages = list(messages)
    compacted = list(compacted_messages)
    kept = list(kept_messages)
    request_metadata = dict(metadata or {})
    limit = _positive_int(
        request_metadata.get("compaction_prompt_context_char_limit"),
        default=context_char_limit,
    )
    source_context = _render_source_context(
        compacted_messages=compacted,
        kept_messages=kept,
        previous_summary=previous_summary,
    )
    bounded_context, truncated = _truncate_text(source_context, limit)
    prompt_metadata = {
        "source_message_count": len(source_messages),
        "source_part_count": _part_count(source_messages),
        "compacted_message_count": len(compacted),
        "compacted_part_count": _part_count(compacted),
        "kept_message_count": len(kept),
        "kept_part_count": _part_count(kept),
        "previous_summary_present": previous_summary is not None,
        "source_context_char_limit": limit,
        "source_context_original_chars": len(source_context),
        "source_context_rendered_chars": len(bounded_context),
        "source_context_truncated": truncated,
    }
    compacted_count = request_metadata.get(
        "compacted_message_count",
        prompt_metadata["compacted_message_count"],
    )
    kept_count = prompt_metadata["kept_message_count"]
    prompt = "\n".join(
        [
            "You are updating a EFP runtime session summary for later turns.",
            "",
            "Return only Markdown using this exact section order:",
            *COMPACTION_SUMMARY_HEADINGS,
            "",
            "Use concise bullets or `(none)` for empty sections.",
            "Preserve exact paths, commands, errors, identifiers, tool names, and message ids.",
            "Merge the previous summary with the new source, remove stale details, and keep next steps actionable.",
            "Do not mention that the summary was generated or that history was compacted.",
            "",
            "Session metadata:",
            f"- session_id: {session_id}",
            f"- source_message_count: {prompt_metadata['source_message_count']}",
            f"- compacted_message_count: {compacted_count}",
            f"- kept_message_count: {kept_count}",
            f"- compacted_part_count: {request_metadata.get('compacted_part_count', prompt_metadata['compacted_part_count'])}",
            f"- compacted_tool_pair_count: {request_metadata.get('compacted_tool_pair_count', 0)}",
            f"- source_context_truncated: {truncated}",
            "",
            "Source context:",
            bounded_context or "(none)",
        ]
    )
    return prompt, prompt_metadata


def render_anchored_compaction_summary(
    *,
    session_id: str,
    compacted_messages: Iterable[Message],
    kept_messages: Iterable[Message] = (),
    previous_summary: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """Render a conservative deterministic summary with stable anchors."""

    compacted = list(compacted_messages)
    kept = list(kept_messages)
    request_metadata = dict(metadata or {})
    part_count = int(
        request_metadata.get("compacted_part_count") or _part_count(compacted)
    )
    message_count = int(
        request_metadata.get("compacted_message_count") or len(compacted)
    )
    tool_pair_count = int(request_metadata.get("compacted_tool_pair_count") or 0)
    source_message_ids = _source_message_ids(compacted)
    relevant_files = extract_relevant_files([*compacted, *kept])
    previous_context = _bounded_previous_summary(previous_summary)
    critical_items = [
        (
            f"- Compacted: {part_count}p,{message_count}m,"
            f"{tool_pair_count} pairs."
        ),
        "- Source message ids: " + (", ".join(source_message_ids) or "(none)"),
    ]
    if session_id:
        critical_items.append(f"- Session id: {session_id}")
    if previous_context:
        critical_items.extend(
            [
                "- Previous summary context:",
                _indent_block(previous_context),
            ]
        )

    return "\n".join(
        [
            "## Goal",
            "(none)",
            "## Constraints & Preferences",
            "(none)",
            "## Progress",
            "### Done",
            "(none)",
            "### In Progress",
            "(none)",
            "### Blocked",
            "(none)",
            "## Key Decisions",
            "(none)",
            "## Next Steps",
            "(none)",
            "## Critical Context\n" + "\n".join(critical_items),
            "## Relevant Files\n" + _render_list_or_none(relevant_files),
        ]
    )


def latest_compaction_summary(messages: Iterable[Message]) -> str | None:
    """Return the latest summary already present in source history."""

    latest: str | None = None
    for message in messages:
        for part in message.parts:
            if part.type is MessagePartType.COMPACTION and part.compaction is not None:
                summary = part.compaction.summary or part.text
                if summary:
                    latest = summary
    return latest


def extract_relevant_files(messages: Iterable[Message]) -> list[str]:
    """Extract obvious workspace paths from message text and tool payloads."""

    paths: list[str] = []
    seen: set[str] = set()
    for message in messages:
        for part in message.parts:
            for text in _part_text_sources(part):
                for match in _PATH_PATTERN.findall(text):
                    path = match.rstrip(".,;)]}'\"")
                    if "://" in path or path in seen:
                        continue
                    seen.add(path)
                    paths.append(path)
                    if len(paths) >= RELEVANT_FILE_LIMIT:
                        return paths
    return paths


def _render_source_context(
    *,
    compacted_messages: list[Message],
    kept_messages: list[Message],
    previous_summary: str | None,
) -> str:
    sections: list[str] = []
    if previous_summary:
        sections.extend(["Previous summary:", previous_summary.strip()])
    sections.extend(
        [
            "Messages to summarize:",
            _render_messages_for_prompt(compacted_messages),
            "Messages kept verbatim:",
            _render_messages_for_prompt(kept_messages),
        ]
    )
    return "\n\n".join(section for section in sections if section)


def _summary_url(url: object) -> str:
    """Render an attachment url for summaries, collapsing inline data: URIs to a
    short placeholder so a base64 image payload never bloats a compaction summary."""
    text = str(url or "")
    if text.startswith("data:"):
        head = text[5:].split(",", 1)[0]
        return f"[inline {head or 'data'}]"
    return text


def _render_messages_for_prompt(messages: list[Message]) -> str:
    if not messages:
        return "(none)"
    rendered: list[str] = []
    for message in messages:
        rendered.append(
            (
                f"- message_id={message.message_id} role={message.role.value} "
                f"status={message.status} session_id={message.session_id}"
            )
        )
        if message.parent_message_id:
            rendered.append(f"  parent_message_id={message.parent_message_id}")
        for index, part in enumerate(message.parts, start=1):
            rendered.extend(_render_part_for_prompt(index, part))
    return "\n".join(rendered)


def _render_part_for_prompt(index: int, part: MessagePart) -> list[str]:
    prefix = f"  part[{index}] id={part.part_id} type={part.type.value}"
    if part.type is MessagePartType.TEXT:
        return [f"{prefix} text={_single_line(part.text)}"]
    if part.type is MessagePartType.REASONING:
        return [f"{prefix} reasoning={_single_line(part.reasoning)}"]
    if part.type is MessagePartType.ERROR:
        return [f"{prefix} error={_single_line(part.text)}"]
    if part.type is MessagePartType.TOOL_CALL and part.tool_call is not None:
        return [
            (
                f"{prefix} call_id={part.tool_call.call_id} "
                f"tool={part.tool_call.tool_name} status={part.tool_call.status} "
                f"arguments={_json_line(part.tool_call.arguments)}"
            )
        ]
    if part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None:
        return [
            (
                f"{prefix} call_id={part.tool_result.call_id} "
                f"tool={part.tool_result.tool_name} status={part.tool_result.status} "
                f"success={part.tool_result.success} error={_single_line(part.tool_result.error)} "
                f"content={_single_line(part.tool_result.content)}"
            )
        ]
    if part.type is MessagePartType.COMPACTION and part.compaction is not None:
        return [
            (
                f"{prefix} source_message_ids={_json_line(part.compaction.source_message_ids)} "
                f"summary={_single_line(part.compaction.summary)}"
            )
        ]
    if part.type is MessagePartType.TASK and part.task is not None:
        return [
            (
                f"{prefix} task_id={part.task.task_id} status={part.task.status} "
                f"prompt={_single_line(part.task.prompt)}"
            )
        ]
    if part.type is MessagePartType.ATTACHMENT and part.attachment is not None:
        return [
            (
                f"{prefix} attachment_id={part.attachment.attachment_id} "
                f"filename={part.attachment.filename} url={_summary_url(part.attachment.url)} "
                f"text_ref={part.attachment.text_ref}"
            )
        ]
    return [prefix]


def _part_text_sources(part: MessagePart) -> list[str]:
    sources: list[str] = []
    if part.text:
        sources.append(part.text)
    if part.reasoning:
        sources.append(part.reasoning)
    if part.tool_call is not None:
        sources.extend([part.tool_call.arguments_text, _json_line(part.tool_call.arguments)])
    if part.tool_result is not None:
        sources.extend(
            [
                part.tool_result.content,
                part.tool_result.error or "",
                _json_line(part.tool_result.output),
            ]
        )
    if part.compaction is not None:
        sources.append(part.compaction.summary)
    if part.task is not None:
        sources.append(part.task.prompt)
    if part.attachment is not None:
        sources.extend(
            [
                part.attachment.filename or "",
                _summary_url(part.attachment.url),
                part.attachment.text_ref or "",
            ]
        )
    return [source for source in sources if source]


def _source_message_ids(messages: list[Message]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for message in messages:
        if message.message_id in seen:
            continue
        seen.add(message.message_id)
        ids.append(message.message_id)
    return ids


def _part_count(messages: Iterable[Message]) -> int:
    return sum(len(message.parts) for message in messages)


def _bounded_previous_summary(previous_summary: str | None) -> str:
    if not previous_summary:
        return ""
    previous, truncated = _truncate_text(
        previous_summary.strip(),
        PREVIOUS_SUMMARY_CONTEXT_CHAR_LIMIT,
    )
    if truncated:
        return previous
    return previous_summary.strip()


def _truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if limit < 1:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    marker = "\n[truncated]"
    if limit <= len(marker):
        return marker[:limit], True
    return text[: limit - len(marker)].rstrip() + marker, True


def _positive_int(value: Any, *, default: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return resolved if resolved > 0 else default


def _json_line(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _single_line(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _render_list_or_none(items: list[str]) -> str:
    if not items:
        return "(none)"
    return "\n".join(f"- {item}" for item in items)


def _indent_block(text: str) -> str:
    return "\n".join(f"  {line}" if line else "" for line in text.splitlines())


__all__ = [
    "COMPACTION_SUMMARY_HEADINGS",
    "DEFAULT_COMPACTION_PROMPT_CONTEXT_CHAR_LIMIT",
    "build_compaction_prompt",
    "extract_relevant_files",
    "latest_compaction_summary",
    "render_anchored_compaction_summary",
]
