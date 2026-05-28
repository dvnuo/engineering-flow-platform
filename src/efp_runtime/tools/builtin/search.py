"""Workspace-contained search tools for EFP Runtime v2."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path

DEFAULT_SEARCH_MATCHES = 100
MAX_DISPLAY_LINE_LENGTH = 2000


def create_grep_tool(workspace_root: str | Path) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        base_path = resolve_workspace_path(root, args.get("path") or ".")
        include = args.get("include")
        include_patterns = _expand_include_patterns(include)
        include_root = base_path if base_path.is_dir() else base_path.parent
        case_sensitive = args.get("case_sensitive", True)
        max_matches = args.get("max_matches", DEFAULT_SEARCH_MATCHES)
        if max_matches < 1:
            raise ValueError("max_matches must be at least 1.")

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"Invalid grep pattern: {exc}") from exc

        matches: list[dict[str, Any]] = []
        mtimes: dict[str, int] = {}
        files_searched = 0
        for file_path in _iter_search_files(root, base_path):
            if include_patterns and not _matches_include(
                file_path,
                include_root=include_root,
                patterns=include_patterns,
            ):
                continue
            files_searched += 1
            text = file_path.read_bytes().decode("utf-8", errors="replace")
            relative_path = workspace_relative_path(root, file_path)
            mtimes[relative_path] = file_path.stat().st_mtime_ns
            for line_number, line in enumerate(text.splitlines(), start=1):
                for match in compiled.finditer(line):
                    matches.append(
                        {
                            "path": relative_path,
                            "line_number": line_number,
                            "column": match.start() + 1,
                            "line": _truncate_display_line(line),
                        }
                    )

        matches.sort(
            key=lambda item: (
                -mtimes.get(str(item["path"]), 0),
                str(item["path"]),
                int(item["line_number"]),
                int(item["column"]),
            )
        )
        total_matches = len(matches)
        returned_matches = min(total_matches, max_matches)
        truncated = total_matches > max_matches
        visible_matches = matches[:max_matches]
        output = {
            "pattern": pattern,
            "path": workspace_relative_path(root, base_path),
            "matches": visible_matches,
            "files_searched": files_searched,
            "truncated": truncated,
            "include": include,
            "total_matches": total_matches,
            "returned_matches": returned_matches,
        }
        return ToolResult(
            call_id=context.tool_call_id or "grep",
            tool_name="grep",
            content=_format_grep_content(
                matches=visible_matches,
                total_matches=total_matches,
                returned_matches=returned_matches,
                truncated=truncated,
            ),
            output=output,
            metadata={
                "include": include,
                "total_matches": total_matches,
                "returned_matches": returned_matches,
                "truncated": truncated,
            },
            truncated=truncated,
        )

    return ToolDef(
        id="grep",
        description="Search workspace files with a regular expression.",
        input_schema={
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "include": {"type": "string"},
                "case_sensitive": {"type": "boolean"},
                "max_matches": {"type": "integer"},
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


def create_glob_tool(workspace_root: str | Path) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        if not pattern:
            raise ValueError("pattern must not be empty.")
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError("Glob pattern must stay inside the workspace root.")

        base_path = resolve_workspace_path(root, args.get("path") or ".")
        if not base_path.exists():
            raise FileNotFoundError(f"Glob path does not exist: {workspace_relative_path(root, base_path)}")
        if not base_path.is_dir():
            raise NotADirectoryError(f"Glob path is not a directory: {workspace_relative_path(root, base_path)}")

        max_matches = args.get("max_matches", DEFAULT_SEARCH_MATCHES)
        if max_matches < 1:
            raise ValueError("max_matches must be at least 1.")

        all_matches = _sorted_glob_matches(root, base_path, pattern)
        truncated = len(all_matches) > max_matches
        matches = all_matches[:max_matches]
        output = {
            "pattern": pattern,
            "path": workspace_relative_path(root, base_path),
            "matches": matches,
            "paths": matches,
            "truncated": truncated,
        }
        return ToolResult(
            call_id=context.tool_call_id or "glob",
            tool_name="glob",
            content=_format_glob_content(
                matches=matches,
                total_matches=len(all_matches),
                truncated=truncated,
            ),
            output=output,
            metadata={
                "total_matches": len(all_matches),
                "returned_matches": len(matches),
                "truncated": truncated,
            },
            truncated=truncated,
        )

    return ToolDef(
        id="glob",
        description="Find workspace paths matching a glob pattern.",
        input_schema={
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "max_matches": {"type": "integer"},
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


def _iter_search_files(workspace_root: Path, base_path: Path) -> Iterator[Path]:
    if not base_path.exists():
        raise FileNotFoundError(f"Search path does not exist: {workspace_relative_path(workspace_root, base_path)}")
    if base_path.is_file():
        yield base_path
        return
    if not base_path.is_dir():
        raise ValueError(f"Search path is not a file or directory: {workspace_relative_path(workspace_root, base_path)}")

    for dirpath, dirnames, filenames in os.walk(base_path, topdown=True, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [
            dirname
            for dirname in sorted(dirnames, key=lambda value: (value.casefold(), value))
            if _is_contained(workspace_root, current / dirname)
        ]
        for filename in sorted(filenames, key=lambda value: (value.casefold(), value)):
            file_path = current / filename
            if not _is_contained(workspace_root, file_path):
                continue
            if file_path.is_file():
                yield file_path.resolve(strict=False)


def _expand_include_patterns(include: str | None) -> list[str]:
    if not include:
        return []
    if Path(include).is_absolute() or ".." in Path(include).parts:
        raise ValueError("include pattern must stay inside the search root.")
    return _expand_braces(include)


def _expand_braces(pattern: str) -> list[str]:
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    end = pattern.find("}", start + 1)
    if end == -1:
        return [pattern]

    prefix = pattern[:start]
    suffix = pattern[end + 1 :]
    alternatives = pattern[start + 1 : end].split(",")
    expanded: list[str] = []
    for alternative in alternatives:
        for value in _expand_braces(f"{prefix}{alternative}{suffix}"):
            expanded.append(value)
    return expanded


def _matches_include(
    file_path: Path,
    *,
    include_root: Path,
    patterns: list[str],
) -> bool:
    try:
        relative = file_path.relative_to(include_root)
    except ValueError:
        return False
    value = relative.as_posix()
    return any(fnmatchcase(value, pattern) for pattern in patterns)


def _truncate_display_line(line: str) -> str:
    if len(line) <= MAX_DISPLAY_LINE_LENGTH:
        return line
    return f"{line[:MAX_DISPLAY_LINE_LENGTH]}..."


def _format_grep_content(
    *,
    matches: list[dict[str, Any]],
    total_matches: int,
    returned_matches: int,
    truncated: bool,
) -> str:
    if total_matches == 0:
        return "No files found"

    header = f"Found {total_matches} matches"
    if truncated:
        header = f"{header} (showing first {returned_matches})"
    lines = [header]
    current_path = ""
    for match in matches:
        match_path = str(match["path"])
        if match_path != current_path:
            if current_path:
                lines.append("")
            current_path = match_path
            lines.append(f"{match_path}:")
        lines.append(f"  Line {match['line_number']}: {match['line']}")

    if truncated:
        hidden = total_matches - returned_matches
        lines.extend(
            [
                "",
                (
                    f"(Results truncated: showing {returned_matches} of "
                    f"{total_matches} matches ({hidden} hidden). Consider using "
                    "a more specific path or pattern.)"
                ),
            ]
        )
    return "\n".join(lines)


def _sorted_glob_matches(
    workspace_root: Path,
    base_path: Path,
    pattern: str,
) -> list[str]:
    matches = {
        workspace_relative_path(workspace_root, match): match.stat().st_mtime_ns
        for match in base_path.glob(pattern)
        if _is_contained(workspace_root, match) and match.exists()
    }
    return [
        path
        for path, _mtime in sorted(
            matches.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def _format_glob_content(
    *,
    matches: list[str],
    total_matches: int,
    truncated: bool,
) -> str:
    if not matches:
        return "No files found"
    lines = list(matches)
    if truncated:
        lines.extend(
            [
                "",
                (
                    f"(Results are truncated: showing first {len(matches)} of "
                    f"{total_matches} results. Consider using a more specific "
                    "path or pattern.)"
                ),
            ]
        )
    return "\n".join(lines)


def _is_contained(workspace_root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(workspace_root)
    except ValueError:
        return False
    return True
