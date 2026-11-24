"""Tell the agent where every attached file sits and what of it reached the model.

The Portal composer uploads each attachment to the runtime, which stores it
under the workspace as ``uploads/<file_id>.<ext>`` and keeps the member's own
file name in the upload metadata. What the chat model gets depends on the
type: documents and text are parsed and an excerpt travels in the prompt
(``inject_context``, cut to a budget), images reach the model as image parts
only when the deployment switch says the chat model may see them
(``src/efp_runtime/llm/vision.py``, off by default), and the office formats
the runtime does not parse (doc, dot, xls) reach it not at all. Whatever the
type, the member may ask for the file to be processed (converted, computed
over, turned into a new file), which takes the file itself rather than an
excerpt. So every attachment is listed with its workspace path and the agent
is told to read it from there with its tools.

The files the model cannot read carry extra wording about how to analyze
them, in this order:

1. An image, when the Image analysis connector is configured: the
   inspect-image CLI (AI Platform), run from bash in the pod.
2. Otherwise, and for the office formats always: the attached-files analysis
   skill (Productivity Suite in the member's own browser), which needs the
   Local bridge connector and therefore an interactive chat. It is also what
   an image falls back to when inspect-image fails.
3. Otherwise the agent says which files it could not read.

The block is model-facing (the transcript keeps the member's own words and
the attachment chips), so it travels as the transient model message.
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
# Image analysis is configured and there are images the model cannot read:
# inspect-image first, then what to do when it fails.
HANDOFF_IMAGES_INSPECT = (
    "Image analysis is on for this assistant: read each image marked above as not readable with the inspect-image "
    "CLI before you answer. Run `inspect-image inspect --image <path> --prompt \"<what to read or explain>\" --json` "
    "from bash and answer from data.result.answer and data.result.visible_text; add --preset ocr, ui, diagram, "
    "chart, or error when it fits, and ask a follow-up question with another call. Never pass --model, never guess "
    "what an image shows, and never fall back to OCR or image-parsing scripts."
)
HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE = (
    "If inspect-image answers auth_required or config_error, or still fails after one retry, analyze the images "
    "with the attached-files analysis skill named in your instructions instead (Productivity Suite through the "
    "user's own browser, the browser tool's workflow.run), asking the user before uploading."
)
HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE = (
    "If inspect-image answers auth_required or config_error, or still fails after one retry, tell the user which "
    "images you could not read and that the Image analysis connector under Portal > Connectors needs checking, "
    "and continue with what the message says in words."
)
# The office formats next to those images: inspect-image does not read them,
# so Productivity Suite stays their only route.
HANDOFF_OFFICE_WITH_BRIDGE = (
    "inspect-image reads images only. For the office files marked above as not readable, the Local bridge is on "
    "for this chat: analyze them with the attached-files analysis skill named in your instructions (Productivity "
    "Suite through the user's own browser, the browser tool's workflow.run). Ask the user before uploading and "
    "report the result as that system's output."
)
HANDOFF_OFFICE_WITHOUT_BRIDGE = (
    "inspect-image reads images only. The Local bridge is not on for this chat, so the office files marked above "
    "as not readable cannot be analyzed here: tell the user which files you could not read and that an "
    "interactive chat with the Local bridge switched on can analyze them."
)
# Image analysis is not configured, the bridge is off and there are images:
# the one thing the member can change for a chat without a browser.
HANDOFF_IMAGE_ANALYSIS_HINT = (
    "Images can also be read without the bridge once the Image analysis connector is set up under Portal > "
    "Connectors."
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


def is_image_attachment(item: Mapping[str, Any]) -> bool:
    """An image by its stored content type; any other unreadable file is an office format."""
    return str(item.get("content_type") or "").strip().lower().startswith("image/")


def handoff_texts(
    *,
    unreadable_images: bool,
    unreadable_office_files: bool,
    bridge_available: bool,
    image_analysis_configured: bool,
) -> list[str]:
    """What follows ``HANDOFF_HEADER``, by which unreadable files there are and the two flags."""
    if image_analysis_configured and unreadable_images:
        texts = [
            HANDOFF_IMAGES_INSPECT,
            HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE if bridge_available else HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE,
        ]
        if unreadable_office_files:
            texts.append(HANDOFF_OFFICE_WITH_BRIDGE if bridge_available else HANDOFF_OFFICE_WITHOUT_BRIDGE)
        return texts
    # Office files only, or no Image analysis connector: Productivity Suite
    # through the bridge is the one route there is.
    texts = [HANDOFF_WITH_BRIDGE if bridge_available else HANDOFF_WITHOUT_BRIDGE]
    if unreadable_images and not bridge_available and not image_analysis_configured:
        texts.append(HANDOFF_IMAGE_ANALYSIS_HINT)
    return texts


def build_attachment_handoff(
    files: Iterable[Mapping[str, Any]],
    *,
    bridge_available: bool,
    image_analysis_configured: bool,
) -> str:
    """The prompt block that lists every attachment with its workspace path.

    Each item carries ``path``, ``content_type``, ``size_bytes``, the member's
    ``name`` and a ``status`` from ``STATUS_NOTES``. The files marked
    ``unreadable`` add the analysis wording: images go to the inspect-image
    CLI when the Image analysis connector is configured
    (``image_analysis_configured``), everything else, and an image that
    inspect-image could not read, to the attached-files analysis skill when
    the Local bridge is on for this chat (``bridge_available``).
    """
    items = [item for item in files if str(item.get("path") or "").strip()]
    if not items:
        return ""
    parts = [ATTACHED_FILES_HEADER, "\n".join(describe_attached_file(item) for item in items), ATTACHED_FILES_RULE]
    unreadable = [item for item in items if str(item.get("status") or "") == "unreadable"]
    if unreadable:
        texts = handoff_texts(
            unreadable_images=any(is_image_attachment(item) for item in unreadable),
            unreadable_office_files=any(not is_image_attachment(item) for item in unreadable),
            bridge_available=bridge_available,
            image_analysis_configured=image_analysis_configured,
        )
        parts.append(" ".join([HANDOFF_HEADER, *texts]))
    return "\n".join(parts)
