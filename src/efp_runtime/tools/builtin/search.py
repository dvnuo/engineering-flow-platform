"""Workspace-contained search tools for EFP runtime."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
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
RG_GIT_EXCLUDES = ("!.git/*", "!**/.git/**")


def create_grep_tool(workspace_root: str | Path) -> ToolDef:
    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        base_path = resolve_workspace_path(root, args.get("path") or ".")
        include = args.get("include")
        include_patterns = _expand_include_patterns(include)
        include_root = base_path if base_path.is_dir() else base_path.parent
        case_sensitive = True
        max_matches = DEFAULT_SEARCH_MATCHES

        if not base_path.exists():
            raise FileNotFoundError(f"Search path does not exist: {workspace_relative_path(root, base_path)}")
        if not base_path.is_file() and not base_path.is_dir():
            raise ValueError(f"Search path is not a file or directory: {workspace_relative_path(root, base_path)}")

        search_result = await _grep_with_rg(
            root,
            base_path,
            pattern=pattern,
            include_patterns=include_patterns,
            include_root=include_root,
            case_sensitive=case_sensitive,
        )
        if search_result is None:
            search_result = _grep_with_python(
                root,
                base_path,
                pattern=pattern,
                include_patterns=include_patterns,
                include_root=include_root,
                case_sensitive=case_sensitive,
            )

        matches = search_result["matches"]
        files_searched = search_result["files_searched"]
        _sort_matches(matches, search_result["mtimes"])
        total_matches = len(matches)
        returned_matches = min(total_matches, max_matches)
        truncated = total_matches > max_matches
        visible_matches = matches[:max_matches]
        content = _format_grep_content(
            matches=visible_matches,
            total_matches=total_matches,
            returned_matches=returned_matches,
            truncated=truncated,
        )
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
            content=content,
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

        max_matches = DEFAULT_SEARCH_MATCHES

        all_matches = await _sorted_glob_matches(root, base_path, pattern)
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
    if _is_git_path(workspace_root, base_path):
        return
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
            and not _is_git_path(workspace_root, current / dirname)
        ]
        for filename in sorted(filenames, key=lambda value: (value.casefold(), value)):
            file_path = current / filename
            if not _is_contained(workspace_root, file_path):
                continue
            if _is_git_path(workspace_root, file_path):
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


async def _sorted_glob_matches(
    workspace_root: Path,
    base_path: Path,
    pattern: str,
) -> list[str]:
    rg_matches = await _glob_with_rg(workspace_root, base_path, pattern)
    if rg_matches is not None:
        return rg_matches
    return _glob_with_python(workspace_root, base_path, pattern)


async def _grep_with_rg(
    workspace_root: Path,
    base_path: Path,
    *,
    pattern: str,
    include_patterns: list[str],
    include_root: Path,
    case_sensitive: bool,
) -> dict[str, Any] | None:
    cwd = base_path if base_path.is_dir() else base_path.parent
    targets = ["."] if base_path.is_dir() else [base_path.name]
    args = [
        "--no-config",
        "--json",
        "--hidden",
        "--no-messages",
    ]
    for exclude in RG_GIT_EXCLUDES:
        args.extend(["--glob", exclude])
    if not case_sensitive:
        args.append("--ignore-case")
    for include in include_patterns:
        args.extend(["--glob", include])
    args.extend(["--", pattern, *targets])

    completed = await _run_rg(args, cwd=cwd)
    if completed is None:
        return None

    code, stdout, stderr = completed
    if code not in {0, 1, 2}:
        raise ValueError(_rg_error_message(stderr, code))
    if code == 2 and _looks_like_regex_error(stderr):
        raise ValueError(_rg_error_message(stderr, code))

    matches: list[dict[str, Any]] = []
    mtimes: dict[str, int] = {}
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        row_type = row.get("type")
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if row_type == "match":
            match_path = _rg_workspace_path(workspace_root, cwd, data)
            if match_path is None:
                continue
            relative_path, full_path = match_path
            mtimes[relative_path] = _safe_mtime_ns(full_path)
            line_text = _strip_line_ending(
                str(data.get("lines", {}).get("text", ""))
            )
            line_number = int(data.get("line_number") or 0)
            submatches = data.get("submatches") or [{"start": 0}]
            for submatch in submatches:
                start = 0
                if isinstance(submatch, dict):
                    start = int(submatch.get("start") or 0)
                matches.append(
                    {
                        "path": relative_path,
                        "line_number": line_number,
                        "column": _byte_offset_to_column(line_text, start),
                        "line": _truncate_display_line(line_text),
                    }
                )

    files_searched = _count_search_files(
        workspace_root,
        base_path,
        include_patterns=include_patterns,
        include_root=include_root,
    )
    return {
        "matches": matches,
        "mtimes": mtimes,
        "files_searched": files_searched,
    }


def _count_search_files(
    workspace_root: Path,
    base_path: Path,
    *,
    include_patterns: list[str],
    include_root: Path,
) -> int:
    count = 0
    for file_path in _iter_search_files(workspace_root, base_path):
        if include_patterns and not _matches_include(
            file_path,
            include_root=include_root,
            patterns=include_patterns,
        ):
            continue
        if _read_search_text(file_path) is None:
            continue
        count += 1
    return count


def _grep_with_python(
    workspace_root: Path,
    base_path: Path,
    *,
    pattern: str,
    include_patterns: list[str],
    include_root: Path,
    case_sensitive: bool,
) -> dict[str, Any]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"Invalid grep pattern: {exc}") from exc

    matches: list[dict[str, Any]] = []
    mtimes: dict[str, int] = {}
    files_searched = 0
    for file_path in _iter_search_files(workspace_root, base_path):
        if include_patterns and not _matches_include(
            file_path,
            include_root=include_root,
            patterns=include_patterns,
        ):
            continue
        text = _read_search_text(file_path)
        if text is None:
            continue
        files_searched += 1
        relative_path = workspace_relative_path(workspace_root, file_path)
        mtimes[relative_path] = _safe_mtime_ns(file_path)
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

    return {
        "matches": matches,
        "mtimes": mtimes,
        "files_searched": files_searched,
    }


async def _glob_with_rg(
    workspace_root: Path,
    base_path: Path,
    pattern: str,
) -> list[str] | None:
    args = ["--no-config", "--files", "--hidden", "--no-messages"]
    for exclude in RG_GIT_EXCLUDES:
        args.extend(["--glob", exclude])
    args.extend(["--glob", pattern, "."])
    completed = await _run_rg(args, cwd=base_path)
    if completed is None:
        return None

    code, stdout, stderr = completed
    if code not in {0, 1}:
        raise ValueError(_rg_error_message(stderr, code))

    matches: dict[str, int] = {}
    for line in stdout.splitlines():
        full_path = _resolve_rg_file_path(base_path, line)
        if not _is_contained(workspace_root, full_path):
            continue
        if _is_git_path(workspace_root, full_path):
            continue
        if not full_path.exists():
            continue
        relative_path = workspace_relative_path(workspace_root, full_path)
        matches[relative_path] = _safe_mtime_ns(full_path)
    return [
        path
        for path, _mtime in sorted(
            matches.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def _glob_with_python(
    workspace_root: Path,
    base_path: Path,
    pattern: str,
) -> list[str]:
    matches = {
        workspace_relative_path(workspace_root, match): _safe_mtime_ns(match)
        for match in _iter_glob_matches(base_path, pattern)
        if _is_contained(workspace_root, match)
        and not _is_git_path(workspace_root, match)
        and match.exists()
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


async def _run_rg(args: list[str], *, cwd: Path) -> tuple[int, str, str] | None:
    rg_path = shutil.which("rg")
    if rg_path is None:
        return None
    env = os.environ.copy()
    env.pop("RIPGREP_CONFIG_PATH", None)
    try:
        process = await asyncio.create_subprocess_exec(
            rg_path,
            *args,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None
    stdout_bytes, stderr_bytes = await process.communicate()
    return (
        int(process.returncode or 0),
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


def _rg_workspace_path(
    workspace_root: Path,
    cwd: Path,
    data: dict[str, Any],
) -> tuple[str, Path] | None:
    path_data = data.get("path")
    if not isinstance(path_data, dict):
        return None
    path_text = str(path_data.get("text") or "")
    if not path_text:
        return None
    full_path = _resolve_rg_file_path(cwd, path_text)
    if not _is_contained(workspace_root, full_path):
        return None
    if _is_git_path(workspace_root, full_path):
        return None
    return workspace_relative_path(workspace_root, full_path), full_path


def _resolve_rg_file_path(cwd: Path, value: str) -> Path:
    cleaned = value.replace("\\", "/")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    path = Path(cleaned)
    if path.is_absolute():
        return path.resolve(strict=False)
    return (cwd / path).resolve(strict=False)


def _sort_matches(matches: list[dict[str, Any]], mtimes: dict[str, int]) -> None:
    matches.sort(
        key=lambda item: (
            -mtimes.get(str(item["path"]), 0),
            str(item["path"]),
            int(item["line_number"]),
            int(item["column"]),
        )
    )


def _read_search_text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None


def _iter_glob_matches(base_path: Path, pattern: str) -> Iterator[Path]:
    try:
        yield from base_path.glob(pattern)
    except OSError:
        return


def _safe_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _strip_line_ending(value: str) -> str:
    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\n") or value.endswith("\r"):
        return value[:-1]
    return value


def _byte_offset_to_column(line: str, byte_offset: int) -> int:
    if byte_offset <= 0:
        return 1
    prefix = line.encode("utf-8")[:byte_offset].decode("utf-8", errors="ignore")
    return len(prefix) + 1


def _rg_error_message(stderr: str, code: int) -> str:
    message = stderr.strip() or f"ripgrep failed with code {code}"
    return f"Invalid grep pattern: {message}" if _looks_like_regex_error(stderr) else message


def _looks_like_regex_error(stderr: str) -> bool:
    return "regex parse error" in stderr or "error parsing regexp" in stderr


def _is_contained(workspace_root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(workspace_root)
    except ValueError:
        return False
    return True


def _is_git_path(workspace_root: Path, path: Path) -> bool:
    try:
        relative = path.resolve(strict=False).relative_to(workspace_root)
    except ValueError:
        return False
    return ".git" in relative.parts
