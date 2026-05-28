"""Workspace-contained text editing tool for EFP Runtime v2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...permissions import ASK, PermissionMetadata
from ..definition import ToolContext, ToolDef
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path


def create_edit_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_workspace_path(root, args["path"])
        if not path.exists():
            raise FileNotFoundError(f"File does not exist: {workspace_relative_path(root, path)}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is not a file: {workspace_relative_path(root, path)}")

        old_text = args["old_text"]
        if old_text == "":
            raise ValueError("old_text must not be empty.")

        content = _read_utf8_text(root, path)
        replacement_count = content.count(old_text)
        if replacement_count == 0:
            raise ValueError("old_text was not found in the file.")
        if not args.get("replace_all") and replacement_count > 1:
            raise ValueError("old_text occurs multiple times; set replace_all to replace all matches.")

        new_content = content.replace(old_text, args["new_text"], -1 if args.get("replace_all") else 1)
        encoded = new_content.encode("utf-8")
        path.write_bytes(encoded)
        return {
            "path": workspace_relative_path(root, path),
            "replacement_count": replacement_count if args.get("replace_all") else 1,
            "bytes": len(encoded),
        }

    return ToolDef(
        id="edit",
        description="Replace text in an existing UTF-8 workspace file.",
        input_schema={
            "type": "object",
            "required": ["path", "old_text", "new_text"],
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "replace_all": {"type": "boolean"},
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
