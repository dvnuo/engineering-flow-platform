"""Workspace-contained text editing tool for EFP Runtime v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...permissions import ASK, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef
from .diff_preview import (
    DEFAULT_MAX_PREVIEW_CHARS,
    DEFAULT_MAX_PREVIEW_LINES,
    file_diff_record,
    text_preview,
    unified_diff_preview,
)
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path


def create_edit_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(root, args["filePath"])
        if not path.exists():
            raise FileNotFoundError(f"File does not exist: {workspace_relative_path(root, path)}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is not a file: {workspace_relative_path(root, path)}")

        old_text = args["oldString"]
        if old_text == "":
            raise ValueError("oldString must not be empty.")
        max_diff_lines = args.get("max_diff_lines", DEFAULT_MAX_PREVIEW_LINES)
        max_diff_chars = args.get("max_diff_chars", DEFAULT_MAX_PREVIEW_CHARS)

        content = _read_utf8_text(root, path)
        relative_path = workspace_relative_path(root, path)
        replacement_count = content.count(old_text)
        if replacement_count == 0:
            raise ValueError(
                "oldString was not found in "
                f"{relative_path}. oldString preview: {text_preview(old_text)}. "
                f"file characters: {len(content)}."
            )
        if (
            args["newString"] != old_text
            and not args.get("replaceAll")
            and replacement_count > 1
        ):
            raise ValueError(
                "oldString occurs multiple times "
                f"({replacement_count} times) in {relative_path}; set "
                "replaceAll=true or provide a more precise oldString."
            )

        new_content = content.replace(old_text, args["newString"], -1 if args.get("replaceAll") else 1)
        old_bytes = len(content.encode("utf-8"))
        encoded = new_content.encode("utf-8")
        new_bytes = len(encoded)
        changed = new_content != content
        applied_replacement_count = replacement_count if args.get("replaceAll") else 1

        if changed:
            path.write_bytes(encoded)

        diff, diff_truncated = unified_diff_preview(
            content,
            new_content,
            f"a/{relative_path}",
            f"b/{relative_path}",
            max_diff_lines,
            max_diff_chars,
        )
        filediff = file_diff_record(
            path=relative_path,
            old_text=content,
            new_text=new_content,
            patch=diff,
        )
        output = {
            "path": relative_path,
            "replacement_count": applied_replacement_count,
            "bytes": new_bytes,
            "old_bytes": old_bytes,
            "new_bytes": new_bytes,
            "diff": diff,
            "diff_truncated": diff_truncated,
            "changed": changed,
            "filediff": filediff,
        }
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name="edit",
            status="success",
            success=True,
            content=_format_edit_content(
                path=relative_path,
                replacement_count=applied_replacement_count,
                bytes_written=new_bytes,
                old_bytes=old_bytes,
                new_bytes=new_bytes,
                changed=changed,
                diff=diff,
                diff_truncated=diff_truncated,
            ),
            output=output,
            metadata={"filediff": filediff},
        )

    return ToolDef(
        id="edit",
        description="Replace oldString with newString in an existing UTF-8 workspace file.",
        input_schema={
            "type": "object",
            "required": ["filePath", "oldString", "newString"],
            "properties": {
                "filePath": {"type": "string"},
                "oldString": {"type": "string"},
                "newString": {"type": "string"},
                "replaceAll": {"type": "boolean"},
                "max_diff_lines": {"type": "integer", "minimum": 0},
                "max_diff_chars": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=permission
        or PermissionMetadata(
            action=ASK,
            reason="Editing files requires approval.",
            category="filesystem",
            resource="workspace",
            risk="medium",
        ),
    )


def _format_edit_content(
    *,
    path: str,
    replacement_count: int,
    bytes_written: int,
    old_bytes: int,
    new_bytes: int,
    changed: bool,
    diff: str,
    diff_truncated: bool,
) -> str:
    if changed:
        summary = (
            f"Edited {path}: replacement_count={replacement_count}, "
            f"bytes={bytes_written}, old_bytes={old_bytes}, new_bytes={new_bytes}."
        )
    else:
        summary = (
            f"No changes made to {path}: replacement_count={replacement_count}, "
            f"bytes={bytes_written}, old_bytes={old_bytes}, new_bytes={new_bytes}; "
            "file content is unchanged."
        )

    parts = [summary, "", "Diff preview:"]
    if diff:
        parts.extend(["```diff", diff.rstrip("\n"), "```"])
    elif diff_truncated:
        parts.append("(truncated to an empty preview)")
    else:
        parts.append("(no diff)")

    if diff_truncated:
        parts.append("Diff preview truncated by max_diff_lines/max_diff_chars.")

    return "\n".join(parts)


def _read_utf8_text(workspace_root: Path, path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data:
        raise ValueError(f"File is not a text file: {workspace_relative_path(workspace_root, path)}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"File is not valid UTF-8 text: {workspace_relative_path(workspace_root, path)}"
        ) from exc
