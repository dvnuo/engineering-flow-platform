"""Workspace-contained filesystem tools for EFP Runtime v2."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from ...instructions import ReadInstructionResolver
from ...permissions import ALLOW, ASK, PermissionMetadata
from ..definition import ToolContext, ToolDef


def normalize_workspace_root(workspace_root: str | Path) -> Path:
    """Return a resolved workspace root directory."""

    root = Path(workspace_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Workspace root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Workspace root is not a directory: {root}")
    return root


def resolve_workspace_path(workspace_root: str | Path, path_value: str | Path) -> Path:
    """Resolve a user path and reject anything outside the workspace root."""

    root = normalize_workspace_root(workspace_root)
    raw_path = Path(path_value)
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path escapes workspace root.") from exc
    return resolved


def workspace_relative_path(workspace_root: str | Path, path: str | Path) -> str:
    """Return a stable POSIX-style path relative to the workspace root."""

    root = normalize_workspace_root(workspace_root)
    path_obj = Path(path)
    try:
        relative = path_obj.relative_to(root)
    except ValueError:
        relative = path_obj.resolve(strict=False).relative_to(root)
    text = relative.as_posix()
    return text or "."


def create_read_file_tool(
    workspace_root: str | Path,
    *,
    instruction_resolver: ReadInstructionResolver | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_workspace_path(root, args["path"])
        if not path.exists():
            raise FileNotFoundError(f"File does not exist: {workspace_relative_path(root, path)}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is not a file: {workspace_relative_path(root, path)}")

        encoding = args.get("encoding") or "utf-8"
        data = path.read_bytes()
        text = data.decode(encoding, errors="replace")
        range_requested = "offset" in args or "limit" in args

        if range_requested:
            offset = args.get("offset", 1)
            limit = args.get("limit")
            _validate_read_range(offset=offset, limit=limit)
            content, range_metadata = _read_text_range(
                text,
                offset=offset,
                limit=limit,
                encoding=encoding,
            )
        else:
            content = text
            range_metadata = {}

        output = {
            "path": workspace_relative_path(root, path),
            "content": content,
            "encoding": encoding,
            "bytes": len(data),
        }
        output.update(range_metadata)
        if instruction_resolver is not None:
            instructions = instruction_resolver.resolve_for_path(path)
            if instructions:
                output["instructions"] = instructions
                output["loaded_instruction_paths"] = [
                    str(entry["path"]) for entry in instructions
                ]
        return output

    return ToolDef(
        id="read_file",
        description="Read a text file inside the workspace, optionally by line range.",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "encoding": {"type": "string"},
                "offset": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="filesystem",
            resource="workspace",
            risk="low",
        ),
    )


def _validate_read_range(*, offset: int, limit: int | None) -> None:
    if offset < 1:
        raise ValueError("offset must be greater than or equal to 1.")
    if limit is not None and limit < 0:
        raise ValueError("limit must be greater than or equal to 0.")


def _read_text_range(
    text: str,
    *,
    offset: int,
    limit: int | None,
    encoding: str,
) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    start_index = offset - 1

    if start_index >= total_lines:
        content = ""
        line_count = 0
        end_line = offset - 1
        has_more = False
        next_offset = None
        range_truncated = False
    else:
        end_index = (
            total_lines if limit is None else min(start_index + limit, total_lines)
        )
        selected_lines = lines[start_index:end_index]
        content = "".join(selected_lines)
        line_count = len(selected_lines)
        end_line = offset + line_count - 1 if line_count else offset - 1
        has_more = end_index < total_lines
        next_offset = end_index + 1 if has_more else None
        range_truncated = limit is not None and has_more

    metadata = {
        "start_line": offset,
        "end_line": end_line,
        "total_lines": total_lines,
        "line_count": line_count,
        "has_more": has_more,
        "next_offset": next_offset,
        "range_truncated": range_truncated,
        "returned_bytes": len(content.encode(encoding, errors="replace")),
    }
    return content, metadata


def create_list_dir_tool(workspace_root: str | Path) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_workspace_path(root, args.get("path") or ".")
        if not path.exists():
            raise FileNotFoundError(f"Directory does not exist: {workspace_relative_path(root, path)}")
        if not path.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {workspace_relative_path(root, path)}")

        entries = [_directory_entry(root, entry) for entry in sorted(path.iterdir(), key=_sort_key)]
        return {
            "path": workspace_relative_path(root, path),
            "entries": entries,
        }

    return ToolDef(
        id="list_dir",
        description="List directory entries inside the workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="filesystem",
            resource="workspace",
            risk="low",
        ),
    )


def create_write_file_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = resolve_workspace_path(root, args["path"])
        parent = path.parent
        if args.get("create_dirs"):
            parent.mkdir(parents=True, exist_ok=True)
        elif not parent.exists():
            raise FileNotFoundError(
                f"Parent directory does not exist: {workspace_relative_path(root, parent)}"
            )
        if not parent.is_dir():
            raise NotADirectoryError(
                f"Parent path is not a directory: {workspace_relative_path(root, parent)}"
            )

        content = args["content"]
        encoding = args.get("encoding") or "utf-8"
        mode = "a" if args.get("append") else "w"
        with path.open(mode, encoding=encoding) as handle:
            handle.write(content)
        return {
            "path": workspace_relative_path(root, path),
            "encoding": encoding,
            "bytes": len(content.encode(encoding)),
            "append": bool(args.get("append")),
        }

    return ToolDef(
        id="write_file",
        description="Write a text file inside the workspace.",
        input_schema={
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "encoding": {"type": "string"},
                "append": {"type": "boolean"},
                "create_dirs": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=permission
        or PermissionMetadata(
            action=ASK,
            reason="Writing files requires approval.",
            category="filesystem",
            resource="workspace",
            risk="medium",
        ),
    )


def create_filesystem_tools(
    workspace_root: str | Path,
    *,
    write_permission: PermissionMetadata | None = None,
    instruction_resolver: ReadInstructionResolver | None = None,
) -> list[ToolDef]:
    return [
        create_read_file_tool(
            workspace_root,
            instruction_resolver=instruction_resolver,
        ),
        create_list_dir_tool(workspace_root),
        create_write_file_tool(workspace_root, permission=write_permission),
    ]


def _directory_entry(workspace_root: Path, path: Path) -> dict[str, Any]:
    file_stat = path.lstat()
    mode = file_stat.st_mode
    if stat.S_ISDIR(mode):
        entry_type = "directory"
    elif stat.S_ISREG(mode):
        entry_type = "file"
    elif stat.S_ISLNK(mode):
        entry_type = "symlink"
    else:
        entry_type = "other"

    return {
        "name": path.name,
        "path": workspace_relative_path(workspace_root, path),
        "type": entry_type,
        "size": file_stat.st_size if entry_type == "file" else None,
    }


def _sort_key(path: Path) -> tuple[str, str]:
    return (path.name.casefold(), path.name)
