"""Repository cache and overview tools for EFP Runtime v2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import unicodedata
from typing import Any
from urllib.parse import urlparse

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef
from .filesystem import (
    normalize_workspace_root,
    resolve_workspace_path,
    workspace_relative_path,
)

GIT_COMMAND_TIMEOUT_SECONDS = 60
GIT_QUERY_TIMEOUT_SECONDS = 10
MAX_STRUCTURE_ENTRIES = 200
IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "dist",
        "build",
        ".next",
        "target",
        "vendor",
    }
)
DEPENDENCY_FILENAMES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "bun.lock",
        "bun.lockb",
        "pnpm-lock.yaml",
        "yarn.lock",
        "requirements.txt",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
        "build.gradle",
        "build.gradle.kts",
        "pom.xml",
        "composer.json",
    }
)
ECOSYSTEM_BY_DEPENDENCY_FILE = {
    "package.json": "Node.js",
    "package-lock.json": "Node.js",
    "bun.lock": "Node.js",
    "bun.lockb": "Node.js",
    "pnpm-lock.yaml": "Node.js",
    "yarn.lock": "Node.js",
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "Gemfile": "Ruby",
    "build.gradle": "Java/Kotlin",
    "build.gradle.kts": "Java/Kotlin",
    "pom.xml": "Java/Kotlin",
    "composer.json": "PHP",
}
ECOSYSTEM_ORDER = ("Node.js", "Python", "Go", "Rust", "Ruby", "Java/Kotlin", "PHP")
NODE_PACKAGE_MANAGER_LOCKS = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
    ("package-lock.json", "npm"),
)
COMMON_ENTRYPOINTS = (
    "index.ts",
    "index.js",
    "src/index.ts",
    "src/index.js",
    "src/main.ts",
    "src/main.js",
    "src/main.py",
    "main.py",
    "app.py",
    "src/app.py",
    "main.go",
    "cmd/main.go",
)
GITHUB_SHORTHAND_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class RepositorySpec:
    repository: str
    remote: str
    label: str


@dataclass(frozen=True)
class GitCommandResult:
    args: tuple[str, ...]
    cwd: Path | None
    exit_code: int
    stdout: str
    stderr: str


class GitCommandError(RuntimeError):
    """Raised when a git subprocess exits unsuccessfully."""

    def __init__(self, result: GitCommandResult):
        command = "git " + " ".join(shlex.quote(arg) for arg in result.args)
        message = f"{command} failed with exit code {result.exit_code}"
        detail = (result.stderr.strip() or result.stdout.strip())[-2000:]
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.result = result


def create_repo_clone_tool(workspace_root: str | Path) -> ToolDef:
    """Create the repo_clone built-in tool."""

    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        spec = _repository_spec(root, args["repository"])
        target_path = _clone_target_path(root, spec, args.get("target_dir"))
        branch_arg = _clean_optional_string(args.get("branch"))
        refresh = bool(args.get("refresh", False))

        if target_path.exists():
            if not target_path.is_dir():
                raise ValueError(
                    "Repository target exists and is not a directory: "
                    f"{workspace_relative_path(root, target_path)}"
                )
            if not (target_path / ".git").is_dir():
                raise ValueError(
                    "Repository target already exists but is not a git repository: "
                    f"{workspace_relative_path(root, target_path)}"
                )
            if refresh:
                await _run_git(
                    ("fetch", "--all", "--tags", "--prune"),
                    cwd=target_path,
                    timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                )
                if branch_arg:
                    await _run_git(
                        ("checkout", branch_arg),
                        cwd=target_path,
                        timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                    )
                await _run_git(
                    ("pull", "--ff-only"),
                    cwd=target_path,
                    timeout=GIT_COMMAND_TIMEOUT_SECONDS,
                )
                status = "refreshed"
            else:
                status = "cached"
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            clone_args = ["clone"]
            if branch_arg:
                clone_args.extend(["--branch", branch_arg])
            clone_args.extend([spec.remote, str(target_path)])
            await _run_git(
                tuple(clone_args),
                cwd=root,
                timeout=GIT_COMMAND_TIMEOUT_SECONDS,
            )
            status = "cloned"

        output = await _clone_output(
            root,
            target_path,
            spec=spec,
            status=status,
            branch_arg=branch_arg,
            refresh=refresh,
        )
        return ToolResult(
            call_id=context.tool_call_id or "repo_clone",
            tool_name="repo_clone",
            content=_format_clone_content(output),
            output=output,
            metadata=dict(output),
        )

    return ToolDef(
        id="repo_clone",
        description="Prepare a git repository in a workspace-local cache.",
        input_schema={
            "type": "object",
            "required": ["repository"],
            "properties": {
                "repository": {"type": "string"},
                "refresh": {"type": "boolean"},
                "branch": {"type": "string"},
                "target_dir": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="repository",
            resource="workspace",
            risk="medium",
            data={"subject_arg": "repository"},
        ),
    )


def create_repo_overview_tool(workspace_root: str | Path) -> ToolDef:
    """Create the repo_overview built-in tool."""

    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        repository_arg = _clean_optional_string(args.get("repository"))
        path_arg = _clean_optional_string(args.get("path"))
        if not repository_arg and not path_arg:
            raise ValueError("repo_overview requires either repository or path.")
        if repository_arg and path_arg:
            raise ValueError("repo_overview accepts either repository or path, not both.")

        if path_arg:
            inspect_path = resolve_workspace_path(root, path_arg)
            repository = None
        else:
            spec = _repository_spec(root, repository_arg or "")
            inspect_path = _repository_cache_path(root, spec)
            repository = spec.repository
            if not inspect_path.is_dir() or not (inspect_path / ".git").is_dir():
                raise FileNotFoundError(
                    f"Cached repository not found for {repository}; use repo_clone first."
                )

        if not inspect_path.exists():
            raise FileNotFoundError(
                f"Repository overview path does not exist: "
                f"{workspace_relative_path(root, inspect_path)}"
            )
        if not inspect_path.is_dir():
            raise NotADirectoryError(
                f"Repository overview path is not a directory: "
                f"{workspace_relative_path(root, inspect_path)}"
            )

        depth = _clamp_depth(args.get("depth", 3))
        structure, truncated = _collect_structure(inspect_path, depth=depth)
        dependency_files = _dependency_files(inspect_path)
        ecosystems = _ecosystems(dependency_files)
        package_manager = _package_manager(dependency_files)
        entrypoints = _entrypoints(inspect_path, dependency_files)
        branch, head = await _git_branch_and_head(inspect_path)
        output = {
            "path": workspace_relative_path(root, inspect_path),
            "repository": repository,
            "branch": branch,
            "head": head,
            "package_manager": package_manager,
            "ecosystems": ecosystems,
            "dependency_files": dependency_files,
            "entrypoints": entrypoints,
            "depth": depth,
            "truncated": truncated,
            "structure": structure,
        }
        return ToolResult(
            call_id=context.tool_call_id or "repo_overview",
            tool_name="repo_overview",
            content=_format_overview_content(output),
            output=output,
            metadata=dict(output),
            truncated=truncated,
        )

    return ToolDef(
        id="repo_overview",
        description=(
            "Inspect a workspace directory or cached repository and return a "
            "concise structure and dependency overview."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "repository": {"type": "string"},
                "path": {"type": "string"},
                "depth": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="repository",
            resource="workspace",
            risk="low",
            data={"subject_arg": "repository"},
        ),
    )


def _repository_spec(workspace_root: Path, repository: str) -> RepositorySpec:
    value = repository.strip()
    if not value:
        raise ValueError("repository must not be empty.")

    local_path = _existing_local_path(workspace_root, value)
    if local_path is not None:
        remote = str(local_path)
        return RepositorySpec(repository=value, remote=remote, label=remote)

    if _is_full_url(value):
        return RepositorySpec(repository=value, remote=value, label=value)

    if _is_github_shorthand(value):
        suffix = "" if value.endswith(".git") else ".git"
        remote = f"https://github.com/{value}{suffix}"
        return RepositorySpec(repository=value, remote=remote, label=remote)

    raise ValueError(
        "repository must be an existing local path, a full URL, or GitHub "
        "shorthand like owner/repo."
    )


def _existing_local_path(workspace_root: Path, value: str) -> Path | None:
    raw_path = Path(value).expanduser()
    candidates = [raw_path] if raw_path.is_absolute() else [workspace_root / raw_path]
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved.exists():
            if not resolved.is_dir():
                raise ValueError(f"Repository local path is not a directory: {value}")
            return resolved
    return None


def _is_full_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme and (parsed.netloc or parsed.scheme == "file"))


def _is_github_shorthand(value: str) -> bool:
    if not GITHUB_SHORTHAND_PATTERN.match(value):
        return False
    owner, name = value.split("/", 1)
    return owner not in (".", "..") and name not in (".", "..")


def _repository_cache_root(workspace_root: Path) -> Path:
    return workspace_root / ".efp_runtime" / "repositories"


def _repository_cache_path(workspace_root: Path, spec: RepositorySpec) -> Path:
    return _repository_cache_root(workspace_root) / _safe_cache_dir_name(spec.label)


def _safe_cache_dir_name(label: str) -> str:
    normalized = unicodedata.normalize("NFKD", label)
    ascii_label = normalized.encode("ascii", errors="ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_label).strip("._-").lower()
    safe = safe or "repository"
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]
    return f"{safe[:64].strip('._-') or 'repository'}-{digest}"


def _clone_target_path(
    workspace_root: Path,
    spec: RepositorySpec,
    target_dir: Any,
) -> Path:
    target_value = _clean_optional_string(target_dir)
    if target_value is None:
        return _repository_cache_path(workspace_root, spec)
    if Path(target_value).is_absolute():
        raise ValueError("target_dir must be workspace-relative.")
    return resolve_workspace_path(workspace_root, target_value)


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _run_git(
    args: tuple[str, ...],
    *,
    cwd: Path | None,
    timeout: int,
    check: bool = True,
) -> GitCommandResult:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd) if cwd is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable not found.") from exc

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        command = "git " + " ".join(shlex.quote(arg) for arg in args)
        raise TimeoutError(
            f"{command} timed out after {timeout} seconds."
        ) from exc

    result = GitCommandResult(
        args=tuple(args),
        cwd=cwd,
        exit_code=int(process.returncode or 0),
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
    )
    if check and result.exit_code != 0:
        raise GitCommandError(result)
    return result


async def _clone_output(
    workspace_root: Path,
    repository_path: Path,
    *,
    spec: RepositorySpec,
    status: str,
    branch_arg: str | None,
    refresh: bool,
) -> dict[str, Any]:
    branch, head = await _git_branch_and_head(repository_path)
    remote = await _git_remote(repository_path)
    return {
        "repository": spec.repository,
        "remote": remote or spec.remote,
        "local_path": workspace_relative_path(workspace_root, repository_path),
        "status": status,
        "branch": branch or branch_arg,
        "head": head,
        "refresh": refresh,
    }


async def _git_remote(path: Path) -> str | None:
    result = await _run_git(
        ("config", "--get", "remote.origin.url"),
        cwd=path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
        check=False,
    )
    if result.exit_code != 0:
        return None
    remote = result.stdout.strip()
    return remote or None


async def _git_branch_and_head(path: Path) -> tuple[str | None, str | None]:
    inside = await _run_git(
        ("rev-parse", "--is-inside-work-tree"),
        cwd=path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
        check=False,
    )
    if inside.exit_code != 0 or inside.stdout.strip() != "true":
        return None, None

    branch_result = await _run_git(
        ("branch", "--show-current"),
        cwd=path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
        check=False,
    )
    branch = branch_result.stdout.strip() if branch_result.exit_code == 0 else ""
    if not branch:
        abbrev_result = await _run_git(
            ("rev-parse", "--abbrev-ref", "HEAD"),
            cwd=path,
            timeout=GIT_QUERY_TIMEOUT_SECONDS,
            check=False,
        )
        if abbrev_result.exit_code == 0:
            branch = abbrev_result.stdout.strip()
    if branch == "HEAD":
        branch = ""

    head_result = await _run_git(
        ("rev-parse", "HEAD"),
        cwd=path,
        timeout=GIT_QUERY_TIMEOUT_SECONDS,
        check=False,
    )
    head = head_result.stdout.strip() if head_result.exit_code == 0 else ""
    return branch or None, head or None


def _clamp_depth(value: Any) -> int:
    if value is None:
        return 3
    depth = int(value)
    return max(1, min(6, depth))


def _collect_structure(root: Path, *, depth: int) -> tuple[list[str], bool]:
    entries: list[str] = []
    truncated = False

    def add_entry(value: str) -> bool:
        nonlocal truncated
        if len(entries) >= MAX_STRUCTURE_ENTRIES:
            truncated = True
            return False
        entries.append(value)
        return True

    def walk(directory: Path, current_depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        try:
            children = sorted(directory.iterdir(), key=_path_sort_key)
        except OSError:
            return
        for child in children:
            if truncated or _is_ignored_path(child):
                continue
            try:
                relative = child.relative_to(root).as_posix()
            except ValueError:
                continue
            if child.is_symlink():
                continue
            if child.is_dir():
                if not add_entry(f"{relative}/"):
                    return
                if current_depth < depth:
                    walk(child, current_depth + 1)
            elif child.is_file():
                if not add_entry(relative):
                    return

    walk(root, 1)
    return entries, truncated


def _dependency_files(root: Path) -> list[str]:
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [
            dirname
            for dirname in sorted(dirnames, key=_name_sort_key)
            if dirname not in IGNORED_DIRECTORIES
            and not (current / dirname).is_symlink()
        ]
        for filename in sorted(filenames, key=_name_sort_key):
            if filename not in DEPENDENCY_FILENAMES:
                continue
            file_path = current / filename
            if file_path.is_symlink() or not file_path.is_file():
                continue
            matches.append(file_path.relative_to(root).as_posix())
    return matches


def _ecosystems(dependency_files: list[str]) -> list[str]:
    detected = {
        ECOSYSTEM_BY_DEPENDENCY_FILE[Path(path).name]
        for path in dependency_files
        if Path(path).name in ECOSYSTEM_BY_DEPENDENCY_FILE
    }
    return [ecosystem for ecosystem in ECOSYSTEM_ORDER if ecosystem in detected]


def _package_manager(dependency_files: list[str]) -> str | None:
    filenames = {Path(path).name for path in dependency_files}
    for filename, manager in NODE_PACKAGE_MANAGER_LOCKS:
        if filename in filenames:
            return manager
    return None


def _entrypoints(root: Path, dependency_files: list[str]) -> list[str]:
    entrypoints: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value or value in seen:
            return
        seen.add(value)
        entrypoints.append(value)

    for dependency_file in dependency_files:
        if Path(dependency_file).name != "package.json":
            continue
        package_json = root / dependency_file
        package_dir = Path(dependency_file).parent
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(package, dict):
            continue
        for field in ("main", "module", "types"):
            value = package.get(field)
            if isinstance(value, str):
                add(_package_relative_entrypoint(package_dir, value))
        bin_value = package.get("bin")
        if isinstance(bin_value, str):
            add(_package_relative_entrypoint(package_dir, bin_value))
        elif isinstance(bin_value, dict):
            for value in bin_value.values():
                if isinstance(value, str):
                    add(_package_relative_entrypoint(package_dir, value))
        for value in _export_entrypoint_values(package.get("exports")):
            add(_package_relative_entrypoint(package_dir, value))

    for entrypoint in COMMON_ENTRYPOINTS:
        candidate = root / entrypoint
        if candidate.is_file() and not candidate.is_symlink():
            add(entrypoint)

    return entrypoints


def _export_entrypoint_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        if value.startswith("."):
            values.append(value)
    elif isinstance(value, list):
        for item in value:
            values.extend(_export_entrypoint_values(item))
    elif isinstance(value, dict):
        for item in value.values():
            values.extend(_export_entrypoint_values(item))
    return values


def _package_relative_entrypoint(package_dir: Path, value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    combined = PurePosixPath(package_dir.as_posix()) / PurePosixPath(text)
    parts: list[str] = []
    for part in combined.parts:
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    return "/".join(parts) or "."


def _is_ignored_path(path: Path) -> bool:
    return path.name in IGNORED_DIRECTORIES


def _path_sort_key(path: Path) -> tuple[int, str, str]:
    directory_rank = 0 if path.is_dir() and not path.is_symlink() else 1
    return (directory_rank, path.name.casefold(), path.name)


def _name_sort_key(value: str) -> tuple[str, str]:
    return (value.casefold(), value)


def _format_clone_content(output: dict[str, Any]) -> str:
    lines = [
        f"Repository {output['status']}: {output['repository']}",
        f"Remote: {output['remote']}",
        f"Local path: {output['local_path']}",
    ]
    if output.get("branch"):
        lines.append(f"Branch: {output['branch']}")
    if output.get("head"):
        lines.append(f"Head: {output['head']}")
    return "\n".join(lines)


def _format_overview_content(output: dict[str, Any]) -> str:
    lines = [f"Repository overview: {output['path']}"]
    if output.get("repository"):
        lines.append(f"Repository: {output['repository']}")
    if output.get("branch"):
        lines.append(f"Branch: {output['branch']}")
    if output.get("head"):
        lines.append(f"Head: {output['head']}")
    if output.get("ecosystems"):
        lines.append(f"Ecosystems: {', '.join(output['ecosystems'])}")
    if output.get("package_manager"):
        lines.append(f"Package manager: {output['package_manager']}")
    if output.get("dependency_files"):
        lines.extend(["", "Dependency files:", *[f"- {path}" for path in output["dependency_files"]]])
    if output.get("entrypoints"):
        lines.extend(["", "Entrypoints:", *[f"- {path}" for path in output["entrypoints"]]])
    lines.extend(["", f"Structure (depth {output['depth']}):"])
    if output.get("structure"):
        lines.extend(f"- {path}" for path in output["structure"])
    else:
        lines.append("- <empty>")
    if output.get("truncated"):
        lines.append(f"Structure truncated at {MAX_STRUCTURE_ENTRIES} entries.")
    return "\n".join(lines)
