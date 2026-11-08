"""Tell the agent where every attached file sits and what of it reached the model.

The Portal composer uploads each attachment to the runtime, which stores it
under the workspace as ``uploads/<file_id>.<ext>`` and keeps the member's own
file name in the upload metadata. What the chat model gets depends on the
type: documents and text are parsed and an excerpt travels in the prompt
(``inject_context``, cut to a budget), images reach the model as image parts
only when the provider can see them, and the office formats the runtime does
not parse (doc, dot, xls) reach it not at all. Whatever the type, the member
may ask for the file to be processed (converted, computed over, turned into a
new file), which takes the file itself rather than an excerpt. So every
attachment is listed with its workspace path and the agent is told to read it
from there with its tools; the files the model cannot read carry the extra
wording about the attached-files analysis skill (Productivity Suite in the
member's own browser, which needs the Local bridge connector). The block is
model-facing (the transcript keeps the member's own words and the attachment
chips), so it travels as the transient model message.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

ATTACHED_FILES_HEADER = (
    "Attached files: the user attached these to this message; each one is stored in the workspace at the path shown:"
)
ATTACHED_FILES_RULE = (
    "An excerpt in this prompt is a preview of a parsed file and may be cut short. To read a whole file, to process "
    "it (convert, filter, compute over it, or produce a new file from it), or to answer what the excerpt does not "
    "cover, read the file from its workspace path with your file and shell tools; never ask the user where the file "
    "is or to upload it again."
)
HANDOFF_HEADER = (
    "The chat model cannot read the files marked above as not readable (images, and office formats the runtime "
    "does not parse)."
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

# What a listed file says about itself after its type and size: what of it
# reached the model in this prompt. ``unreadable`` files get the handoff
# wording as well.
STATUS_NOTES = {
    "excerpt": "an excerpt of it is in this prompt",
    "not_in_prompt": "parsed, but no excerpt of it is in this prompt; read it from its path",
    "not_parsed": "it could not be parsed, read it from its path",
    "image_sent": "sent to the model as an image",
    "unreadable": "not readable by the chat model",
}


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
    note = STATUS_NOTES.get(str(item.get("status") or ""))
    tail = f"; {note}" if note else ""
    return f"- {path} ({', '.join(details)}{tail})"


def build_attachment_handoff(
    files: Iterable[Mapping[str, Any]],
    *,
    bridge_available: bool,
) -> str:
    """The prompt block that lists every attachment with its workspace path.

    Each item carries ``path``, ``content_type``, ``size_bytes``, the member's
    ``name`` and a ``status`` from ``STATUS_NOTES``. The files marked
    ``unreadable`` add the attached-files analysis wording, which depends on
    whether the Local bridge is on for this chat.
    """
    items = [item for item in files if str(item.get("path") or "").strip()]
    if not items:
        return ""
    parts = [ATTACHED_FILES_HEADER, "\n".join(describe_attached_file(item) for item in items), ATTACHED_FILES_RULE]
    if any(str(item.get("status") or "") == "unreadable" for item in items):
        parts.append(f"{HANDOFF_HEADER} {HANDOFF_WITH_BRIDGE if bridge_available else HANDOFF_WITHOUT_BRIDGE}")
    return "\n".join(parts)
