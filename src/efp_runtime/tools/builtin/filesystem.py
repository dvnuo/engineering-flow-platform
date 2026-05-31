"""Workspace-contained filesystem tools for EFP runtime."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from ...instructions import ReadInstructionResolver
from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef
from .diff_preview import (
    DEFAULT_MAX_PREVIEW_CHARS,
    DEFAULT_MAX_PREVIEW_LINES,
    file_diff_record,
    unified_diff_preview,
)

READ_DEFAULT_LIMIT = 2000
READ_MAX_VISIBLE_BYTES = 50 * 1024
READ_MAX_LINE_LENGTH = 2000
READ_MAX_LINE_SUFFIX = (
    f"... (line truncated to {READ_MAX_LINE_LENGTH} chars)"
)
READ_BINARY_SAMPLE_BYTES = 4096


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


def create_read_tool(
    workspace_root: str | Path,
    *,
    instruction_resolver: ReadInstructionResolver | None = None,
) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(root, args["filePath"])
        if not path.exists():
            raise FileNotFoundError(_missing_path_message(root, path))

        if path.is_file():
            return _read_workspace_file(
                root,
                path,
                args=args,
                context=context,
                instruction_resolver=instruction_resolver,
            )
        if path.is_dir():
            return _read_workspace_directory(root, path, args=args, context=context)

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


def _read_workspace_file(
    workspace_root: Path,
    path: Path,
    *,
    args: dict[str, Any],
    context: ToolContext,
    instruction_resolver: ReadInstructionResolver | None,
) -> ToolResult:
    relative_path = workspace_relative_path(workspace_root, path)
    encoding = "utf-8"
    data, text = _read_text_file_strict(workspace_root, path, encoding)
    offset = args.get("offset", 1)
    default_limit_applied = "limit" not in args
    limit = args.get("limit", READ_DEFAULT_LIMIT)
    _validate_read_range(offset=offset, limit=limit)
    content, range_metadata = _read_text_for_display_range(
        text,
        offset=offset,
        limit=limit,
        encoding=encoding,
        default_limit_applied=default_limit_applied,
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
    _attach_read_instructions(output, instruction_resolver, path, context)
    content_truncated = range_metadata["range_truncated"] or bool(
        range_metadata["truncated_by"]
    )
    return ToolResult(
        call_id=context.tool_call_id or "read",
        tool_name="read",
        content=_format_read_content(
            path=relative_path,
            content=content,
            range_metadata=range_metadata,
        ),
        output=output,
        metadata={
            "truncated": content_truncated,
            "truncated_by": list(range_metadata["truncated_by"]),
        },
        truncated=content_truncated,
    )


def _read_text_for_display_range(
    text: str,
    *,
    offset: int,
    limit: int,
    encoding: str,
    default_limit_applied: bool,
) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    start_index = offset - 1
    selected_lines: list[str] = []
    returned_bytes = 0
    truncated_by: list[str] = []
    byte_truncated = False

    if start_index >= total_lines:
        if total_lines > 0 or offset != 1:
            raise ValueError(
                f"Offset {offset} is out of range for this file ({total_lines} lines)."
            )
        end_index = start_index
        content = ""
        line_count = 0
        end_line = offset - 1
        has_more = False
        next_offset = None
        range_truncated = False
    else:
        end_index = start_index
        line_limit_index = min(start_index + limit, total_lines)
        for index in range(start_index, line_limit_index):
            line, line_truncated = _truncate_visible_line(lines[index])
            if line_truncated:
                _append_unique(truncated_by, "line_length")

            line_bytes = len(line.encode(encoding, errors="replace"))
            if returned_bytes + line_bytes > READ_MAX_VISIBLE_BYTES:
                byte_truncated = True
                _append_unique(truncated_by, "bytes")
                break

            selected_lines.append(line)
            returned_bytes += line_bytes
            end_index = index + 1

        content = "".join(selected_lines)
        line_count = len(selected_lines)
        end_line = offset + line_count - 1 if line_count else offset - 1
        has_more = end_index < total_lines
        next_offset = end_index + 1 if has_more else None
        if has_more and not byte_truncated:
            _append_unique(truncated_by, "lines")
        range_truncated = has_more

    metadata = {
        "start_line": offset,
        "end_line": end_line,
        "total_lines": total_lines,
        "line_count": line_count,
        "has_more": has_more,
        "next_offset": next_offset,
        "range_truncated": range_truncated,
        "returned_bytes": returned_bytes,
        "default_limit_applied": default_limit_applied,
        "max_visible_bytes": READ_MAX_VISIBLE_BYTES,
        "max_line_length": READ_MAX_LINE_LENGTH,
        "truncated_by": truncated_by,
    }
    return content, metadata


def _truncate_visible_line(line: str) -> tuple[str, bool]:
    body, ending = _split_line_ending(line)
    if len(body) <= READ_MAX_LINE_LENGTH:
        return line, False
    return (
        f"{body[:READ_MAX_LINE_LENGTH]}{READ_MAX_LINE_SUFFIX}{ending}",
        True,
    )


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


def _read_text_file_strict(
    workspace_root: Path,
    path: Path,
    encoding: str,
) -> tuple[bytes, str]:
    relative_path = workspace_relative_path(workspace_root, path)
    data = path.read_bytes()
    if _looks_binary(data[:READ_BINARY_SAMPLE_BYTES]):
        raise ValueError(f"File is binary and cannot be read as text: {relative_path}")
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


def _looks_binary(sample: bytes) -> bool:
    if not sample:
        return False
    if b"\x00" in sample:
        return True

    non_printable = 0
    for byte in sample:
        if byte < 9 or (13 < byte < 32):
            non_printable += 1
    return non_printable / len(sample) > 0.3


def _missing_path_message(workspace_root: Path, path: Path) -> str:
    relative_path = workspace_relative_path(workspace_root, path)
    suggestions = _missing_path_suggestions(workspace_root, path)
    if not suggestions:
        return f"Path does not exist: {relative_path}"
    return (
        f"Path does not exist: {relative_path}\n\n"
        "Did you mean one of these?\n"
        + "\n".join(suggestions)
    )


def _missing_path_suggestions(workspace_root: Path, path: Path) -> list[str]:
    parent = path.parent
    try:
        parent.relative_to(workspace_root)
    except ValueError:
        return []
    if not parent.is_dir():
        return []

    needle = path.name.casefold()
    suggestions: list[str] = []
    for entry in sorted(parent.iterdir(), key=_sort_key):
        name = entry.name.casefold()
        if needle in name or name in needle:
            suggestions.append(workspace_relative_path(workspace_root, entry))
            if len(suggestions) >= 3:
                break
    return suggestions


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _read_workspace_directory(
    workspace_root: Path,
    path: Path,
    *,
    args: dict[str, Any],
    context: ToolContext,
) -> ToolResult:
    relative_path = workspace_relative_path(workspace_root, path)
    offset = args.get("offset", 1)
    default_limit_applied = "limit" not in args
    limit = args.get("limit", READ_DEFAULT_LIMIT)
    all_entries = [
        _directory_entry(workspace_root, entry)
        for entry in sorted(path.iterdir(), key=_sort_key)
    ]
    entries, metadata = _slice_directory_entries(
        all_entries,
        offset=offset,
        limit=limit,
        default_limit_applied=default_limit_applied,
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
            total_entries=len(all_entries),
            range_metadata=metadata,
        ),
        output=output,
        metadata={
            "truncated": metadata["truncated"],
            "default_limit_applied": default_limit_applied,
        },
        truncated=metadata["truncated"],
    )


def _slice_directory_entries(
    entries: list[dict[str, Any]],
    *,
    offset: int,
    limit: int | None,
    default_limit_applied: bool,
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
        "default_limit_applied": default_limit_applied,
    }


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

        encoding = "utf-8"
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
        max_diff_lines = DEFAULT_MAX_PREVIEW_LINES
        max_diff_chars = DEFAULT_MAX_PREVIEW_CHARS
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
        filediff = (
            file_diff_record(
                path=relative_path,
                old_text=old_text,
                new_text=content,
                patch=diff,
            )
            if old_text is not None
            else None
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
        metadata = {}
        if filediff is not None:
            output["filediff"] = filediff
            metadata["filediff"] = filediff
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
            metadata=metadata,
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
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=permission or _default_write_permission(),
    )


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
    context: ToolContext,
) -> None:
    if instruction_resolver is None:
        return
    instructions = instruction_resolver.resolve_for_path(
        path,
        exclude_paths=_system_instruction_paths(context),
    )
    if not instructions:
        return
    output["instructions"] = instructions
    output["loaded_instruction_paths"] = [
        str(entry["path"]) for entry in instructions
    ]


def _system_instruction_paths(context: ToolContext) -> list[str]:
    value = context.metadata.get("system_instruction_paths")
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    return []


def _directory_display_name(entry: dict[str, Any]) -> str:
    name = str(entry["name"])
    if entry.get("type") == "directory":
        return f"{name}/"
    return name


def _format_read_content(
    *,
    path: str,
    content: str,
    range_metadata: dict[str, Any],
) -> str:
    numbered_content = _numbered_read_content(
        content=content,
        range_metadata=range_metadata,
    )
    return "\n".join(
        [
            _inline_tag("path", path),
            _inline_tag("type", "file"),
            _tagged_block("content", numbered_content),
        ]
    )


def _numbered_read_content(*, content: str, range_metadata: dict[str, Any]) -> str:
    start_line = int(range_metadata["start_line"])
    end_line = int(range_metadata["end_line"])
    total_lines = int(range_metadata["total_lines"])
    lines = [
        f"{line_number}: {line}"
        for line_number, line in enumerate(content.splitlines(), start=start_line)
    ]

    if "bytes" in range_metadata.get("truncated_by", []):
        next_offset = range_metadata["next_offset"] or end_line + 1
        marker = (
            f"(Output capped at {READ_MAX_VISIBLE_BYTES // 1024} KB. "
            f"Showing lines {start_line}-{end_line}. Use offset={next_offset} "
            "to continue.)"
        )
    elif range_metadata["has_more"]:
        next_offset = range_metadata["next_offset"] or end_line + 1
        marker = (
            f"(Showing lines {start_line}-{end_line} of {total_lines}. "
            f"Use offset={next_offset} to continue.)"
        )
    else:
        marker = f"(End of file - total {total_lines} lines)"

    return "\n".join([*lines, "", marker])


def _format_read_directory_content(
    *,
    path: str,
    entries: list[str],
    total_entries: int,
    range_metadata: dict[str, Any],
) -> str:
    entries_content = list(entries)
    entries_content.extend(
        [
            "",
            _directory_range_marker(
                shown_entries=len(entries),
                total_entries=total_entries,
                range_metadata=range_metadata,
            ),
        ]
    )
    return "\n".join(
        [
            _inline_tag("path", path),
            _inline_tag("type", "directory"),
            _tagged_block("entries", "\n".join(entries_content)),
        ]
    )


def _directory_range_marker(
    *,
    shown_entries: int,
    total_entries: int,
    range_metadata: dict[str, Any],
) -> str:
    if range_metadata["has_more"]:
        offset = int(range_metadata["offset"])
        next_offset = range_metadata["next_offset"] or offset + shown_entries
        last_entry = offset + shown_entries - 1
        return (
            f"(Showing {shown_entries} of {total_entries} entries. "
            f"Use offset={next_offset} to read beyond entry {last_entry})"
        )
    return f"({total_entries} entries)"


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
        action=ALLOW,
        category="filesystem",
        resource="workspace",
        risk="medium",
    )
