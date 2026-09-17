"""Built-in tool that keeps a member's standing notes across sessions.

Three actions behind one tool id: ``remember`` stores one sentence the member
asked to keep, ``forget`` drops a note by id, ``list`` shows what is kept.
Notes are per member and per assistant; the runtime renders them into the
system prompt on every run, so a remembered preference applies without being
repeated. The tool refuses to work without a member identity rather than
storing notes that could never be shown to the right person again.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from ...member_memory import (
    MemberMemoryError,
    MemberNotesStore,
    notes_to_payload,
    render_note_line,
)
from ...permissions import ALLOW, PermissionMetadata
from ...system_prompt import resolve_session_user
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


MEMORY_TOOL_ID = "memory"
ACTIONS = ("remember", "forget", "list")

TOOL_DESCRIPTION = (
    "Keep, list, or drop short notes about the current member that should apply "
    "in every future session with this assistant: standing preferences and "
    "personal conventions such as reply language, PR description format, branch "
    "prefix, or review habits. Use `remember` only when the member explicitly asks "
    "you to remember something or states a preference meant to last, keep the note "
    "to one sentence, and restate what you saved. Use `forget` with a note id when "
    "they ask you to drop or change a note. Use `list` to show what is kept. Never "
    "store secrets, credentials, other people's personal details, task progress, or "
    "project knowledge that belongs in Confluence, Jira, or the instructions."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["action"],
    "properties": {
        "action": {
            "type": "string",
            "enum": list(ACTIONS),
            "description": "remember: keep `text`. forget: drop `note_id`. list: show all notes.",
        },
        "text": {
            "type": "string",
            "description": (
                "remember: the note to keep, one sentence in the member's own terms, "
                "at most 300 characters."
            ),
        },
        "note_id": {
            "type": "string",
            "description": "forget: the id of the note to remove, as shown in Member notes or by list.",
        },
    },
    "additionalProperties": False,
}


def create_memory_tool(
    store: MemberNotesStore,
    *,
    permission: PermissionMetadata | None = None,
    tool_id: str = MEMORY_TOOL_ID,
) -> ToolDef:
    """Create the ``memory`` tool over ``store``."""

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        action = str(args.get("action") or "").strip()
        member = resolve_session_user(context.metadata)
        member_id = (member or {}).get("id") or ""
        if not member_id:
            return _error_result(
                context,
                tool_id,
                "This run carries no member identity, so notes cannot be kept or read. "
                "Tell the member the preference could not be saved.",
            )
        try:
            if action == "remember":
                note, created = await asyncio.to_thread(
                    store.add_note, member_id, args.get("text")
                )
                notes = await asyncio.to_thread(store.list_notes, member_id)
                payload = _payload(action, notes, store, note=note, created=created)
                content = _render_remember(payload)
            elif action == "forget":
                note_id = str(args.get("note_id") or "").strip()
                if not note_id:
                    return _error_result(context, tool_id, "forget needs the `note_id` of the note to drop.")
                removed = await asyncio.to_thread(store.remove_note, member_id, note_id)
                if removed is None:
                    return _error_result(
                        context,
                        tool_id,
                        f"No note with id {note_id} for this member. Use list to see the current ids.",
                    )
                notes = await asyncio.to_thread(store.list_notes, member_id)
                payload = _payload(action, notes, store, note=removed, created=False)
                content = _render_forget(payload)
            elif action == "list":
                notes = await asyncio.to_thread(store.list_notes, member_id)
                payload = _payload(action, notes, store)
                content = _render_list(payload)
            else:
                return _error_result(
                    context,
                    tool_id,
                    f"Unknown action {action!r}; use one of: {', '.join(ACTIONS)}.",
                )
        except MemberMemoryError as exc:
            return _error_result(context, tool_id, str(exc))

        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name=tool_id,
            status="success",
            success=True,
            content=content,
            output=payload,
            metadata={
                "action": action,
                "note_count": payload["note_count"],
                "note_id": (payload.get("note") or {}).get("note_id"),
                "created": payload.get("created"),
            },
        )

    return ToolDef(
        id=tool_id,
        description=TOOL_DESCRIPTION,
        input_schema=INPUT_SCHEMA,
        execute=execute,
        permission=permission
        or PermissionMetadata(
            action=ALLOW,
            category="memory",
            resource="member_notes",
            risk="low",
        ),
        runtime_metadata={"member_notes_store": store},
    )


def _payload(
    action: str,
    notes: list[Any],
    store: MemberNotesStore,
    *,
    note: Any = None,
    created: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "notes": notes_to_payload(notes),
        "note_count": len(notes),
        "max_notes": store.max_notes,
    }
    if note is not None:
        payload["note"] = note.to_dict()
    if created is not None:
        payload["created"] = created
    return payload


def _render_remember(payload: Mapping[str, Any]) -> str:
    note = payload.get("note") or {}
    usage = f"{payload.get('note_count')} of {payload.get('max_notes')} notes used."
    if payload.get("created"):
        return (
            f"Remembered for this member (note {note.get('note_id')}): \"{note.get('text')}\". "
            f"{usage} Tell the member what you saved."
        )
    return (
        f"Already remembered as note {note.get('note_id')}: \"{note.get('text')}\". "
        f"Nothing changed. {usage}"
    )


def _render_forget(payload: Mapping[str, Any]) -> str:
    note = payload.get("note") or {}
    return (
        f"Forgot note {note.get('note_id')}: \"{note.get('text')}\". "
        f"{payload.get('note_count')} note(s) remain."
    )


def _render_list(payload: Mapping[str, Any]) -> str:
    notes = payload.get("notes") or []
    if not notes:
        return "No notes for this member yet."
    lines = [f"{len(notes)} note(s) for this member (max {payload.get('max_notes')}), newest first:"]
    lines.extend(render_note_line(note) for note in notes)
    return "\n".join(lines)


def _error_result(context: ToolContext, tool_id: str, message: str) -> ToolResult:
    return ToolResult(
        call_id=context.tool_call_id or "",
        tool_name=tool_id,
        status="error",
        success=False,
        error=message,
        content=message,
        output={"error": message},
        metadata={"error": message},
    )


__all__ = ["ACTIONS", "MEMORY_TOOL_ID", "TOOL_DESCRIPTION", "create_memory_tool"]
