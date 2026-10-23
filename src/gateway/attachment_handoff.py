"""Hand the attachments the chat model cannot read to the agent.

The chat model reads the documents and text the runtime parses. It cannot see
images, and the runtime does not parse the office formats Productivity Suite
reads (doc, dot, xls). Those files stay under the workspace and are described
to the agent instead: where each one sits, and whether this chat can analyze
them through the attached-files analysis skill (Productivity Suite in the
member's own browser, which needs the Local bridge connector). The block is
model-facing (the transcript keeps the member's own words and the attachment
chips), so it travels as the transient model message.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

HANDOFF_HEADER = (
    "Attached files the chat model cannot read (images, and office formats the runtime does not parse); "
    "each one sits in the workspace:"
)
HANDOFF_WITH_BRIDGE = (
    "The Local bridge is on for this chat: analyze these files with the attached-files analysis skill named "
    "in your instructions (Productivity Suite through the user's own browser, the browser tool's workflow.run). "
    "Ask the user before uploading, report the result as that system's output, and never guess what a file "
    "shows or fall back to OCR or image-parsing scripts."
)
HANDOFF_WITHOUT_BRIDGE = (
    "The Local bridge is not on for this chat (a background task, a delegation, or the browser toggle is off), "
    "so these files cannot be analyzed here: tell the user which files you could not read and that an "
    "interactive chat with the Local bridge switched on can analyze them, never guess what a file shows, never "
    "fall back to OCR or image-parsing scripts, and continue with what the message says in words."
)


def describe_attached_file(item: Mapping[str, Any]) -> str:
    path = str(item.get("path") or "").strip()
    content_type = str(item.get("content_type") or "file").strip()
    size = item.get("size_bytes")
    name = str(item.get("name") or "").strip()
    details = [content_type]
    if isinstance(size, int) and size >= 0:
        details.append(f"{size} bytes")
    if name and name != path.rsplit("/", 1)[-1] and name != path.rsplit("\\", 1)[-1]:
        details.append(f'attached as "{name}"')
    return f"- {path} ({', '.join(details)})"


def build_attachment_handoff(
    files: Iterable[Mapping[str, Any]],
    *,
    bridge_available: bool,
) -> str:
    """The prompt block that stands in for the files the model cannot read."""
    lines = [describe_attached_file(item) for item in files if str(item.get("path") or "").strip()]
    if not lines:
        return ""
    parts = [HANDOFF_HEADER, "\n".join(lines), HANDOFF_WITH_BRIDGE if bridge_available else HANDOFF_WITHOUT_BRIDGE]
    return "\n".join(parts)
