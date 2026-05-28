"""Workspace-contained filesystem tools for EFP Runtime v2."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from ...instructions import ReadInstructionResolver
from ...permissions import ALLOW, ASK, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef
from .diff_preview import (
    DEFAULT_MAX_PREVIEW_CHARS,
    DEFAULT_MAX_PREVIEW_LINES,
    unified_diff_preview,
)


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
        _attach_read_instructions(output, instruction_resolver, path)
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


def create_read_tool(
    workspace_root: str | Path,
    *,
    instruction_resolver: ReadInstructionResolver | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(root, args["filePath"])
        if not path.exists():
            raise FileNotFoundError(
                f"Path does not exist: {workspace_relative_path(root, path)}"
            )

        if path.is_file():
            return _read_alias_file(
                root,
                path,
                args=args,
                context=context,
                instruction_resolver=instruction_resolver,
            )
        if path.is_dir():
            return _read_alias_directory(root, path, args=args, context=context)

        raise ValueError(
            f"Path is not a file or directory: {workspace_relative_path(root, path)}"
        )

    return ToolDef(
        id="read",
        description=(
            "Read a workspace file by filePath, or list a workspace directory."
        ),
        input_schema={
            "type": "object",
            "required": ["filePath"],
            "properties": {
                "filePath": {"type": "string"},
                "offset": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 0},
                "encoding": {"type": "string"},
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


def _read_alias_file(
    workspace_root: Path,
    path: Path,
    *,
    args: dict[str, Any],
    context: ToolContext,
    instruction_resolver: ReadInstructionResolver | None,
) -> ToolResult:
    relative_path = workspace_relative_path(workspace_root, path)
    encoding = args.get("encoding") or "utf-8"
    data, text = _read_text_file_strict(workspace_root, path, encoding)
    offset = args.get("offset", 1)
    limit = args.get("limit")
    _validate_read_range(offset=offset, limit=limit)
    content, range_metadata = _read_text_range(
        text,
        offset=offset,
        limit=limit,
        encoding=encoding,
    )
    output = {
        "path": relative_path,
        "filePath": relative_path,
        "type": "file",
        "content": content,
        "encoding": encoding,
        "bytes": len(data),
    }
    output.update(range_metadata)
    _attach_read_instructions(output, instruction_resolver, path)
    return ToolResult(
        call_id=context.tool_call_id or "read",
        tool_name="read",
        content=_format_read_file_content(path=relative_path, content=content),
        output=output,
    )


def _read_text_file_strict(
    workspace_root: Path,
    path: Path,
    encoding: str,
) -> tuple[bytes, str]:
    relative_path = workspace_relative_path(workspace_root, path)
    data = path.read_bytes()
    if b"\x00" in data:
        raise ValueError(
            f"File is not a text file (contains null bytes): {relative_path}"
        )
    try:
        text = data.decode(encoding)
    except LookupError as exc:
        raise ValueError(
            f"Unknown text encoding for {relative_path}: {encoding}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"File cannot be decoded as {encoding}: {relative_path}"
        ) from exc
    return data, text


def _read_alias_directory(
    workspace_root: Path,
    path: Path,
    *,
    args: dict[str, Any],
    context: ToolContext,
) -> ToolResult:
    relative_path = workspace_relative_path(workspace_root, path)
    offset = args.get("offset", 1)
    limit = args.get("limit")
    all_entries = [
        _directory_entry(workspace_root, entry)
        for entry in sorted(path.iterdir(), key=_sort_key)
    ]
    entries, metadata = _slice_directory_entries(
        all_entries,
        offset=offset,
        limit=limit,
    )
    display_entries = [_directory_display_name(entry) for entry in entries]
    output = {
        "path": relative_path,
        "filePath": relative_path,
        "type": "directory",
        "entries": entries,
        "total_entries": len(all_entries),
        **metadata,
    }
    return ToolResult(
        call_id=context.tool_call_id or "read",
        tool_name="read",
        content=_format_read_directory_content(
            path=relative_path,
            entries=display_entries,
        ),
        output=output,
    )


def _slice_directory_entries(
    entries: list[dict[str, Any]],
    *,
    offset: int,
    limit: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _validate_read_range(offset=offset, limit=limit)
    total_entries = len(entries)
    start_index = offset - 1
    if start_index >= total_entries:
        selected_entries: list[dict[str, Any]] = []
        has_more = False
        next_offset = None
        truncated = False
    else:
        end_index = (
            total_entries if limit is None else min(start_index + limit, total_entries)
        )
        selected_entries = entries[start_index:end_index]
        has_more = end_index < total_entries
        next_offset = end_index + 1 if has_more else None
        truncated = limit is not None and has_more
    return selected_entries, {
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": next_offset,
        "truncated": truncated,
    }


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


def create_write_tool(
    workspace_root: str | Path,
    *,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(root, args["filePath"])
        relative_path = workspace_relative_path(root, path)
        if path.exists() and not path.is_file():
            if path.is_dir():
                raise IsADirectoryError(f"Path is a directory: {relative_path}")
            raise ValueError(f"Path is not a regular file: {relative_path}")

        parent = path.parent
        if parent.exists() and not parent.is_dir():
            raise NotADirectoryError(
                f"Parent path is not a directory: {workspace_relative_path(root, parent)}"
            )

        encoding = args.get("encoding") or "utf-8"
        content = args["content"]
        encoded = _encode_text_for_write(relative_path, content, encoding)
        existed = path.exists()
        old_data = path.read_bytes() if existed else b""
        old_text = (
            _decode_existing_text_for_diff(old_data, encoding) if existed else ""
        )
        old_bytes = len(old_data)
        new_bytes = len(encoded)
        created = not existed
        changed = created or old_data != encoded
        max_diff_lines = args.get("max_diff_lines", DEFAULT_MAX_PREVIEW_LINES)
        max_diff_chars = args.get("max_diff_chars", DEFAULT_MAX_PREVIEW_CHARS)
        diff = ""
        diff_truncated = False
        if old_text is not None:
            diff, diff_truncated = unified_diff_preview(
                old_text,
                content,
                f"a/{relative_path}",
                f"b/{relative_path}",
                max_diff_lines,
                max_diff_chars,
            )

        parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)

        output = {
            "path": relative_path,
            "filePath": relative_path,
            "encoding": encoding,
            "bytes": new_bytes,
            "old_bytes": old_bytes,
            "new_bytes": new_bytes,
            "changed": changed,
            "created": created,
            "diff": diff,
            "diff_truncated": diff_truncated,
        }
        return ToolResult(
            call_id=context.tool_call_id or "write",
            tool_name="write",
            content=_format_write_content(
                path=relative_path,
                bytes_written=new_bytes,
                old_bytes=old_bytes,
                new_bytes=new_bytes,
                changed=changed,
                created=created,
                diff=diff,
                diff_truncated=diff_truncated,
                diff_unavailable=old_text is None,
            ),
            output=output,
        )

    return ToolDef(
        id="write",
        description="Write a text file to filePath inside the workspace.",
        input_schema={
            "type": "object",
            "required": ["filePath", "content"],
            "properties": {
                "filePath": {"type": "string"},
                "content": {"type": "string"},
                "encoding": {"type": "string"},
                "max_diff_lines": {"type": "integer", "minimum": 0},
                "max_diff_chars": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=permission or _default_write_permission(),
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
        permission=permission or _default_write_permission(),
    )


def create_filesystem_tools(
    workspace_root: str | Path,
    *,
    write_permission: PermissionMetadata | None = None,
    instruction_resolver: ReadInstructionResolver | None = None,
) -> list[ToolDef]:
    return [
        create_read_tool(
            workspace_root,
            instruction_resolver=instruction_resolver,
        ),
        create_read_file_tool(
            workspace_root,
            instruction_resolver=instruction_resolver,
        ),
        create_list_dir_tool(workspace_root),
        create_write_tool(workspace_root, permission=write_permission),
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


def _attach_read_instructions(
    output: dict[str, Any],
    instruction_resolver: ReadInstructionResolver | None,
    path: Path,
) -> None:
    if instruction_resolver is None:
        return
    instructions = instruction_resolver.resolve_for_path(path)
    if not instructions:
        return
    output["instructions"] = instructions
    output["loaded_instruction_paths"] = [
        str(entry["path"]) for entry in instructions
    ]


def _directory_display_name(entry: dict[str, Any]) -> str:
    name = str(entry["name"])
    if entry.get("type") == "directory":
        return f"{name}/"
    return name


def _format_read_file_content(*, path: str, content: str) -> str:
    return "\n".join(
        [
            _inline_tag("path", path),
            _inline_tag("type", "file"),
            _tagged_block("content", content),
        ]
    )


def _format_read_directory_content(*, path: str, entries: list[str]) -> str:
    return "\n".join(
        [
            _inline_tag("path", path),
            _inline_tag("type", "directory"),
            _tagged_block("entries", "\n".join(entries)),
        ]
    )


def _format_write_content(
    *,
    path: str,
    bytes_written: int,
    old_bytes: int,
    new_bytes: int,
    changed: bool,
    created: bool,
    diff: str,
    diff_truncated: bool,
    diff_unavailable: bool,
) -> str:
    state = "created" if created else "updated"
    if not changed:
        state = "unchanged"
    summary = (
        f"Wrote {path}: {state}, bytes={bytes_written}, "
        f"old_bytes={old_bytes}, new_bytes={new_bytes}."
    )
    parts = [summary]
    if diff:
        parts.extend(["", "Diff preview:", "```diff", diff.rstrip("\n"), "```"])
    elif diff_unavailable:
        parts.extend(
            ["", "Diff preview unavailable: existing file is not decodable text."]
        )
    elif diff_truncated:
        parts.extend(["", "Diff preview truncated to an empty preview."])
    if diff_truncated:
        parts.append("Diff preview truncated by max_diff_lines/max_diff_chars.")
    return "\n".join(parts)


def _inline_tag(tag: str, value: str) -> str:
    return f"<{tag}>{value}</{tag}>"


def _tagged_block(tag: str, content: str) -> str:
    if content.endswith("\n"):
        return f"<{tag}>\n{content}</{tag}>"
    return f"<{tag}>\n{content}\n</{tag}>"


def _encode_text_for_write(path: str, content: str, encoding: str) -> bytes:
    try:
        return content.encode(encoding)
    except LookupError as exc:
        raise ValueError(f"Unknown text encoding for {path}: {encoding}") from exc
    except UnicodeEncodeError as exc:
        raise ValueError(f"Content cannot be encoded as {encoding}: {path}") from exc


def _decode_existing_text_for_diff(data: bytes, encoding: str) -> str | None:
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return None


def _default_write_permission() -> PermissionMetadata:
    return PermissionMetadata(
        action=ASK,
        reason="Writing files requires approval.",
        category="filesystem",
        resource="workspace",
        risk="medium",
    )
