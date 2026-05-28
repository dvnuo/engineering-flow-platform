"""Workspace-contained search tools for EFP Runtime v2."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ...permissions import ALLOW, PermissionMetadata
from ..definition import ToolContext, ToolDef
from .filesystem import normalize_workspace_root, resolve_workspace_path, workspace_relative_path


def create_grep_tool(workspace_root: str | Path) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        pattern = args["pattern"]
        base_path = resolve_workspace_path(root, args.get("path") or ".")
        case_sensitive = args.get("case_sensitive", True)
        max_matches = args.get("max_matches", 100)
        if max_matches < 1:
            raise ValueError("max_matches must be at least 1.")

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            raise ValueError(f"Invalid grep pattern: {exc}") from exc

        matches: list[dict[str, Any]] = []
        files_searched = 0
        for file_path in _iter_search_files(root, base_path):
            files_searched += 1
            text = file_path.read_bytes().decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                for match in compiled.finditer(line):
                    matches.append(
                        {
                            "path": workspace_relative_path(root, file_path),
                            "line_number": line_number,
                            "column": match.start() + 1,
                            "line": line,
                        }
                    )
                    if len(matches) >= max_matches:
                        return {
                            "pattern": pattern,
                            "path": workspace_relative_path(root, base_path),
                            "matches": matches,
                            "files_searched": files_searched,
                            "truncated": True,
                        }

        return {
            "pattern": pattern,
            "path": workspace_relative_path(root, base_path),
            "matches": matches,
            "files_searched": files_searched,
            "truncated": False,
        }

    return ToolDef(
        id="grep",
        description="Search workspace files with a regular expression.",
        input_schema={
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
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

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
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

        max_matches = args.get("max_matches")
        if max_matches is not None and max_matches < 1:
            raise ValueError("max_matches must be at least 1.")

        all_matches = sorted(
            {
                workspace_relative_path(root, match)
                for match in base_path.glob(pattern)
                if _is_contained(root, match) and match.exists()
            },
            key=lambda value: (value.casefold(), value),
        )
        truncated = max_matches is not None and len(all_matches) > max_matches
        matches = all_matches[:max_matches] if max_matches is not None else all_matches
        return {
            "pattern": pattern,
            "path": workspace_relative_path(root, base_path),
            "matches": matches,
            "paths": matches,
            "truncated": truncated,
        }

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


def _is_contained(workspace_root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(workspace_root)
    except ValueError:
        return False
    return True
