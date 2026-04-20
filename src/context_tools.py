"""Internal context projection helper tools."""

from __future__ import annotations

from typing import Optional

from src.context_blob_store import read_ref


async def context_read_ref(
    ref: str,
    section: Optional[str] = None,
    start: Optional[int] = None,
    max_chars: int = 6000,
    _session_id: Optional[str] = None,
) -> str:
    return read_ref(ref, session_id=_session_id, section=section, start=start, max_chars=max_chars)


def get_tools_schemas() -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": "context_read_ref",
                "description": (
                    "Reads a previously persisted context blob by ctx:// ref. "
                    "Use this when Jira/Confluence/tool/assistant output was compacted or shown as a preview. "
                    "Prefer specific sections or max_chars <= 8000."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": "ctx://context/... reference"},
                        "section": {"type": "string", "description": "Optional section name (raw, toc, or heading name)"},
                        "start": {"type": "integer", "description": "Optional character offset for pagination"},
                        "max_chars": {"type": "integer", "default": 6000, "description": "Maximum characters to read (prefer <= 8000)"},
                    },
                    "required": ["ref"],
                },
            },
        }
    ]

