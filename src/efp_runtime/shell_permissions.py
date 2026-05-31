"""Lightweight shell command scanning for permission requests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import posixpath
import re
import shlex
from typing import Any


_DYNAMIC_PATH_RE = re.compile(
    r"(\$\(|\$\{|`|\$[A-Za-z_][A-Za-z0-9_]*|^~(?:/|[^/]*))"
)
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*")
_REDIRECT_RE = re.compile(r"^(?:\d+)?(?:>>?|<<?|<>|>&|<&)(?:&?\d+|-|.+)?$")
_GLOB_CHARS = frozenset("*?[")
_SHELL_TOOL_FALLBACK_PATTERN_CHARS = 500


@dataclass(frozen=True)
class ShellPermissionSummary:
    """Structured shell permission scan result."""

    metadata: dict[str, Any]
    patterns: list[str]


def shell_permission_summary(args: Mapping[str, Any]) -> ShellPermissionSummary:
    """Return conservative permission metadata and patterns for a shell call."""

    command = _string_arg(args, "command") or ""
    description = _string_arg(args, "description")
    workdir = _workdir(args)
    command_preview = _preview_text(command)
    metadata: dict[str, Any] = {
        "command_preview": command_preview,
        "description": description if description is not None else "",
        "workdir": workdir,
        "command_names": [],
        "path_args": [],
        "permission_patterns": [],
        "dynamic_paths": False,
        "workspace_escape": False,
    }

    if not command:
        return ShellPermissionSummary(metadata=metadata, patterns=[])

    try:
        _scan_command(command, workdir=workdir, metadata=metadata)
    except ValueError as exc:
        metadata["shell_parse_error"] = str(exc)
        patterns = [_fallback_pattern(command)]
        metadata["permission_patterns"] = list(patterns)
        return ShellPermissionSummary(metadata=metadata, patterns=patterns)

    patterns = _patterns_from_path_args(metadata["path_args"])
    if not patterns:
        patterns = [_fallback_pattern(command)]
    metadata["permission_patterns"] = list(patterns)

    command_names = metadata["command_names"]
    if command_names:
        metadata["command_name"] = command_names[0]

    return ShellPermissionSummary(metadata=metadata, patterns=patterns)


def shell_permission_metadata(args: Mapping[str, Any]) -> dict[str, Any]:
    """Return shell permission request metadata for a shell call."""

    return shell_permission_summary(args).metadata


def shell_permission_patterns(args: Mapping[str, Any]) -> list[str]:
    """Return shell permission request patterns for a shell call."""

    return shell_permission_summary(args).patterns


def _scan_command(
    command: str,
    *,
    workdir: str,
    metadata: dict[str, Any],
) -> None:
    current_workdir = _normalize_workdir(workdir)
    for segment in _split_simple_commands(command):
        if not segment:
            continue
        tokens = shlex.split(segment, posix=True)
        if not tokens:
            continue

        command_name, arg_index = _command_token(tokens)
        if not command_name:
            continue
        metadata["command_names"].append(command_name)

        path_specs = _path_specs(command_name, tokens[arg_index:])
        if command_name in {"cd", "pushd"} and path_specs:
            metadata["cwd_affecting"] = True

        added_entries: list[dict[str, Any]] = []
        for raw, kind in path_specs:
            entry = _path_entry(
                raw=raw,
                command=command_name,
                workdir=current_workdir,
                kind=kind,
            )
            if entry is None:
                continue
            added_entries.append(entry)
            metadata["path_args"].append(entry)
            if entry.get("dynamic"):
                metadata["dynamic_paths"] = True
            if entry.get("workspace_escape"):
                metadata["workspace_escape"] = True

        if command_name in {"cd", "pushd"} and added_entries:
            target = added_entries[0]
            if not target.get("dynamic"):
                current_workdir = str(target["path"])


def _split_simple_commands(command: str) -> list[str]:
    segments: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if command.startswith("&&", index) or command.startswith("||", index):
            segments.append(command[start:index].strip())
            index += 2
            start = index
            continue
        if char in {";", "|", "\n"}:
            segments.append(command[start:index].strip())
            index += 1
            start = index
            continue
        index += 1
    segments.append(command[start:].strip())
    return segments


def _command_token(tokens: list[str]) -> tuple[str | None, int]:
    index = 0
    while index < len(tokens) and _is_assignment(tokens[index]):
        index += 1

    if index < len(tokens) and tokens[index] == "env":
        index += 1
        while index < len(tokens):
            token = tokens[index]
            if _is_assignment(token):
                index += 1
                continue
            if token == "--":
                index += 1
                break
            if token.startswith("-"):
                index += 2 if token in {"-u", "--unset", "-C", "--chdir"} else 1
                continue
            break

    if index >= len(tokens):
        return None, index
    return _basename_command(tokens[index]), index + 1


def _path_specs(command: str, args: list[str]) -> list[tuple[str, str]]:
    if command in {"cd", "pushd"}:
        operands = _non_flag_operands(args, command=command, keep_dash=True)
        return [(operands[0], "cwd")] if operands else []
    if command in {"cat", "ls", "head", "tail", "touch", "mkdir", "rm"}:
        return [(operand, "argument") for operand in _non_flag_operands(args, command=command)]
    if command in {"cp", "mv"}:
        return _copy_move_specs(_non_flag_operands(args, command=command))
    if command in {"chmod", "chown"}:
        operands = _non_flag_operands(args, command=command)
        return [(operand, "argument") for operand in operands[1:]]
    if command == "find":
        return [(operand, "argument") for operand in _find_operands(args)]
    if command == "sed":
        return _sed_specs(args)
    if command in {"grep", "rg"}:
        return [(operand, "argument") for operand in _grep_operands(args, command=command)]
    return []


def _copy_move_specs(operands: list[str]) -> list[tuple[str, str]]:
    if not operands:
        return []
    if len(operands) == 1:
        return [(operands[0], "source")]
    return [
        *((operand, "source") for operand in operands[:-1]),
        (operands[-1], "destination"),
    ]


def _non_flag_operands(
    args: list[str],
    *,
    command: str,
    keep_dash: bool = False,
) -> list[str]:
    operands: list[str] = []
    index = 0
    options_done = False
    while index < len(args):
        token = args[index]
        if not options_done and token == "--":
            options_done = True
            index += 1
            continue
        if not options_done and _is_redirection(token):
            index += 2 if _is_redirection_operator(token) else 1
            continue
        if not options_done and _looks_like_flag(token):
            index += 1 + _flag_value_count(command, token)
            continue
        if token == "-" and not keep_dash:
            index += 1
            continue
        operands.append(token)
        index += 1
    return operands


def _find_operands(args: list[str]) -> list[str]:
    operands: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            continue
        if token in {"-H", "-L", "-P"} or re.match(r"^-O\d+$", token):
            index += 1
            continue
        if token in {"!", "(", ")"} or token.startswith("-"):
            break
        if _is_redirection(token):
            index += 2 if _is_redirection_operator(token) else 1
            continue
        operands.append(token)
        index += 1
    return operands or ["."]


def _sed_specs(args: list[str]) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = []
    script_seen = False
    index = 0
    options_done = False
    while index < len(args):
        token = args[index]
        if not options_done and token == "--":
            options_done = True
            index += 1
            continue
        if not options_done and _is_redirection(token):
            index += 2 if _is_redirection_operator(token) else 1
            continue
        if not options_done and _looks_like_flag(token):
            flag, inline_value = _split_long_flag_value(token)
            if flag in {"-e", "--expression"}:
                script_seen = True
                index += 1 if inline_value is not None or token.startswith("-e") and len(token) > 2 else 2
                continue
            if flag in {"-f", "--file"}:
                script_seen = True
                value = inline_value
                if value is None and token.startswith("-f") and len(token) > 2:
                    value = token[2:]
                if value is None and index + 1 < len(args):
                    value = args[index + 1]
                    index += 2
                else:
                    index += 1
                if value:
                    specs.append((value, "script"))
                continue
            index += 1 + _flag_value_count("sed", token)
            continue
        if not script_seen:
            script_seen = True
            index += 1
            continue
        if token != "-":
            specs.append((token, "argument"))
        index += 1
    return specs


def _grep_operands(args: list[str], *, command: str) -> list[str]:
    operands: list[str] = []
    pattern_seen = False
    index = 0
    options_done = False
    while index < len(args):
        token = args[index]
        if not options_done and token == "--":
            options_done = True
            index += 1
            continue
        if not options_done and _is_redirection(token):
            index += 2 if _is_redirection_operator(token) else 1
            continue
        if not options_done and _looks_like_flag(token):
            flag, inline_value = _split_long_flag_value(token)
            if flag in _grep_pattern_flags(command):
                pattern_seen = True
                if inline_value is not None or _short_flag_has_inline_value(token, flag):
                    index += 1
                else:
                    index += 2
                continue
            index += 1 + _flag_value_count(command, token)
            continue
        if not pattern_seen:
            pattern_seen = True
            index += 1
            continue
        if token != "-":
            operands.append(token)
        index += 1
    return operands


def _flag_value_count(command: str, token: str) -> int:
    flag, inline_value = _split_long_flag_value(token)
    if inline_value is not None:
        return 0
    return 1 if flag in _flags_with_separate_values(command) else 0


def _flags_with_separate_values(command: str) -> set[str]:
    common = {"--color", "--exclude", "--exclude-dir", "--include"}
    if command in {"grep", "rg"}:
        return common | {
            "-A",
            "-B",
            "-C",
            "-D",
            "-e",
            "-f",
            "-g",
            "-m",
            "--after-context",
            "--before-context",
            "--binary-files",
            "--context",
            "--context-separator",
            "--directories",
            "--engine",
            "--file",
            "--glob",
            "--glob-case-insensitive",
            "--iglob",
            "--label",
            "--max-count",
            "--max-depth",
            "--max-filesize",
            "--mmap",
            "--pager",
            "--path-separator",
            "--pre",
            "--regexp",
            "--replace",
            "--sort",
            "--sortr",
            "--type",
            "--type-add",
            "--type-clear",
        }
    if command == "sed":
        return {"-e", "-f", "--expression", "--file"}
    if command in {"cp", "mv"}:
        return {"-S", "-t", "--backup", "--suffix", "--target-directory"}
    if command in {"head", "tail"}:
        return {"-c", "-n", "--bytes", "--lines"}
    if command == "ls":
        return {"-I", "--block-size", "--color", "--format", "--ignore", "--sort", "--time"}
    if command == "mkdir":
        return {"-m", "--mode"}
    if command == "touch":
        return {"-d", "-r", "-t", "--date", "--reference", "--time"}
    if command == "chmod":
        return {"--reference"}
    if command == "chown":
        return {"--from", "--reference"}
    return common


def _grep_pattern_flags(command: str) -> set[str]:
    if command == "rg":
        return {"-e", "-f", "--regexp", "--file"}
    return {"-e", "-f", "--regexp", "--file"}


def _split_long_flag_value(token: str) -> tuple[str, str | None]:
    if token.startswith("--") and "=" in token:
        flag, value = token.split("=", 1)
        return flag, value
    if token.startswith("-") and not token.startswith("--") and len(token) > 2:
        short = token[:2]
        if short in {
            "-A",
            "-B",
            "-C",
            "-D",
            "-I",
            "-c",
            "-d",
            "-e",
            "-f",
            "-g",
            "-m",
            "-n",
            "-r",
            "-S",
            "-t",
        }:
            return short, token[2:]
    return token, None


def _short_flag_has_inline_value(token: str, flag: str) -> bool:
    return token.startswith(flag) and token != flag and not token.startswith("--")


def _path_entry(
    *,
    raw: str,
    command: str,
    workdir: str,
    kind: str,
) -> dict[str, Any] | None:
    if raw == "":
        return None
    dynamic = _is_dynamic_path(raw)
    glob = _is_glob_path(raw)
    path = _normalize_path(raw, workdir=workdir, dynamic=dynamic)
    if path == "":
        return None
    workspace_escape = _workspace_escape(path)
    entry: dict[str, Any] = {
        "raw": raw,
        "path": path,
        "command": command,
        "kind": kind,
    }
    if dynamic:
        entry["dynamic"] = True
    if glob:
        entry["glob"] = True
    if workspace_escape:
        entry["workspace_escape"] = True
    return entry


def _normalize_path(raw: str, *, workdir: str, dynamic: bool) -> str:
    if dynamic and _dynamic_starts_path(raw):
        return _normalize_dynamic_path(raw)
    if _is_absolute_or_home(raw):
        return _normalize_dynamic_path(raw) if dynamic else posixpath.normpath(raw)
    base = _normalize_workdir(workdir)
    if base in {"", "."}:
        return _normalize_dynamic_path(raw) if dynamic else posixpath.normpath(raw)
    joined = posixpath.join(base, raw)
    return _normalize_dynamic_path(joined) if dynamic else posixpath.normpath(joined)


def _normalize_dynamic_path(path: str) -> str:
    while "//" in path:
        path = path.replace("//", "/")
    if path.endswith("/.") and len(path) > 2:
        path = path[:-2]
    return path


def _normalize_workdir(workdir: str) -> str:
    if workdir in {"", "."}:
        return "."
    return posixpath.normpath(workdir)


def _patterns_from_path_args(path_args: list[dict[str, Any]]) -> list[str]:
    patterns: list[str] = []
    seen: set[str] = set()
    for entry in path_args:
        pattern = str(entry.get("path") or "")
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)
    return patterns


def _workdir(args: Mapping[str, Any]) -> str:
    workdir = _string_arg(args, "workdir")
    if workdir:
        return workdir
    cwd = _string_arg(args, "cwd")
    return cwd or "."


def _fallback_pattern(command: str) -> str:
    return command[:_SHELL_TOOL_FALLBACK_PATTERN_CHARS]


def _preview_text(value: str, *, max_chars: int = 240) -> str:
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return f"{text[: max_chars - 3]}..."


def _string_arg(args: Mapping[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    return str(value)


def _basename_command(token: str) -> str:
    if "/" not in token:
        return token
    return posixpath.basename(token.rstrip("/")) or token


def _is_assignment(token: str) -> bool:
    return bool(_ASSIGNMENT_RE.match(token))


def _looks_like_flag(token: str) -> bool:
    return token.startswith("-") and token not in {"-", "--"}


def _is_redirection(token: str) -> bool:
    return bool(_REDIRECT_RE.match(token))


def _is_redirection_operator(token: str) -> bool:
    return token in {">", ">>", "<", "<<", "<>", ">|", "2>", "2>>", "&>", "&>>"}


def _is_dynamic_path(path: str) -> bool:
    return bool(_DYNAMIC_PATH_RE.search(path))


def _dynamic_starts_path(path: str) -> bool:
    return (
        path.startswith("$")
        or path.startswith("`")
        or path.startswith("~")
        or path.startswith("$(")
    )


def _is_glob_path(path: str) -> bool:
    return any(char in path for char in _GLOB_CHARS)


def _is_absolute_or_home(path: str) -> bool:
    return path.startswith("/") or path.startswith("~")


def _workspace_escape(path: str) -> bool:
    return path.startswith("/") or path == ".." or path.startswith("../")
