"""Git repositories cloned in the assistant's workspace.

The Portal's "branches in this workspace" picker asks which repositories the
assistant has on disk and where each one stands: the branch checked out, what
it tracks, how many commits it is ahead. Only the runtime can answer, since
the workspace is its disk.

The scan is deliberately narrow. It looks in the few places a clone lands
(``repos/``, the repository cache of the runtime's own tools, the workspace
root), takes no path from the caller, and runs only local, read-only git
commands under a time budget, so a workspace full of large repositories costs
the pod a bounded few seconds and the endpoint cannot be pointed anywhere.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

# The picker is a short list, and each repository costs several git processes.
MAX_REPOS = 30
# One slow command (status on a huge work tree) must not eat the whole budget,
# and the whole scan must stay well inside the Portal's proxy timeout.
GIT_TIMEOUT_SECONDS = 3.0
TOTAL_BUDGET_SECONDS = 8.0

REPOS_DIRNAME = "repos"
# Where the runtime's repository tools keep their clones.
REPOSITORY_CACHE_PARTS = (".efp_runtime", "repositories")

# Variables that would point every command at one repository, whatever
# directory it runs in. The gateway's own environment is not expected to carry
# them, and a scan that trusted them would describe the wrong repository.
_REPOSITORY_ENVIRONMENT = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_NAMESPACE",
    }
)

# A scheme of one letter is a Windows drive ("C://..."), not a URL.
_URL_PATTERN = re.compile(
    r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]+)://(?P<authority>[^/?#]*)(?P<rest>.*)$",
    re.DOTALL,
)


class _BudgetSpent(Exception):
    """The scan ran out of time; what is collected so far is the answer."""


class _Budget:
    def __init__(self, seconds: float) -> None:
        self._deadline = time.monotonic() + seconds

    def remaining(self) -> float:
        return self._deadline - time.monotonic()

    def check(self) -> None:
        if self.remaining() <= 0:
            raise _BudgetSpent()


def list_workspace_repos(
    workspace_root: Path,
    *,
    max_repos: int = MAX_REPOS,
    git_timeout_seconds: float = GIT_TIMEOUT_SECONDS,
    total_budget_seconds: float = TOTAL_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Return {"repos": [...], "truncated": bool} for one workspace.

    Repositories come newest commit first. ``truncated`` says the list is not
    everything there is: the time budget ran out, or more than ``max_repos``
    were found. Every repository found in time is inspected before the cap is
    applied, so the ones kept are the most recent and not merely the first in
    scan order. Blocking; call it off the event loop.
    """

    budget = _Budget(total_budget_seconds)
    repos: list[dict[str, Any]] = []
    truncated = False
    try:
        root = Path(workspace_root).expanduser().resolve(strict=False)
        for relative_path, directory in _repository_directories(root, budget):
            try:
                entry = _describe_repository(relative_path, directory, budget, git_timeout_seconds)
            except _BudgetSpent:
                raise
            except Exception:
                # One repository in a state nobody foresaw is that
                # repository's problem, not the picker's.
                logger.debug("Skipping workspace repository %s", relative_path, exc_info=True)
                continue
            if entry is not None:
                repos.append(entry)
    except _BudgetSpent:
        truncated = True

    # The timestamps are all UTC in one format, so they sort as text. A
    # repository without a commit has none and goes last; the sort is stable,
    # so equal times keep the scan order.
    repos.sort(key=lambda entry: entry["last_commit_at"] or "", reverse=True)
    if len(repos) > max_repos:
        repos = repos[:max_repos]
        truncated = True
    return {"repos": repos, "truncated": truncated}


def parse_remote(raw: str) -> tuple[str, str, str]:
    """(remote_url, host, repo) for an origin URL as git stores it.

    ``remote_url`` is the URL without its userinfo: a clone made with a token
    in the URL keeps that token in ``.git/config``, and this value leaves the
    pod. ``host`` and ``repo`` are filled for a network remote (a URL with a
    host, or the scp-like ``git@host:owner/name.git``) and are "" for a local
    path or a ``file://`` URL, which name no host a picker could ask.
    """

    url = str(raw or "").strip()
    if not url:
        return "", "", ""
    match = _URL_PATTERN.match(url)
    if match is not None:
        # The userinfo ends at the last "@" of the authority.
        authority = match["authority"].rpartition("@")[2]
        remote_url = f"{match['scheme']}://{authority}{match['rest']}"
        host = "" if match["scheme"].lower() == "file" else _host_of(authority)
        path = re.split(r"[?#]", match["rest"], maxsplit=1)[0]
        return remote_url, host, _repo_of(path) if host else ""
    if _is_local_path(url):
        return url, "", ""
    # What is left is the scp-like "[user@]host:path". The userinfo ends at
    # the last "@" before the first slash; cutting there, rather than matching
    # a well-formed user, means a password somebody wrote into it by mistake
    # does not survive either.
    head, slash, tail = url.partition("/")
    remote_url = head.rpartition("@")[2] + slash + tail
    host, colon, path = remote_url.partition(":")
    if not colon or len(host) < 2 or "/" in host:
        return remote_url, "", ""
    return remote_url, host.lower(), _repo_of(path)


def _is_local_path(url: str) -> bool:
    """Whether git reads a remote that is not a URL as a path on disk.

    Git's own rule: it is scp-like only when a colon comes before any slash.
    A colon in second place is a Windows drive ("C:\\repos\\origin.git").
    """

    colon = url.find(":")
    before = url[: max(colon, 0)]
    return colon < 2 or "/" in before or "\\" in before


def _host_of(authority: str) -> str:
    # The port is left out: for ssh it is the ssh port, which says nothing
    # about the host's web or API address.
    if authority.startswith("["):
        return authority[1:].partition("]")[0].lower()
    return authority.partition(":")[0].lower()


def _repo_of(path: str) -> str:
    repo = path.strip("/")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")].rstrip("/")
    return repo


def _repository_directories(root: Path, budget: _Budget) -> Iterator[tuple[str, Path]]:
    """Yield (workspace-relative path, resolved directory) per repository found.

    Only the fixed places are listed, in order, and each repository once even
    when a link makes it reachable twice. A directory whose resolved path is
    not inside the workspace is skipped: a link out of the workspace must not
    make the endpoint describe a repository the assistant was not given. That
    rule also keeps the workspace root itself out.
    """

    seen: set[Path] = set()
    for directory in _scanned_directories(root, budget):
        if not (directory / ".git").exists():
            continue
        try:
            resolved = directory.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if root not in resolved.parents or resolved in seen:
            continue
        seen.add(resolved)
        yield directory.relative_to(root).as_posix(), resolved


def _scanned_directories(root: Path, budget: _Budget) -> Iterator[Path]:
    """Every directory that may be a repository: repos/*, repos/*/*, the cache, the root's children."""

    # repos/<name> and repos/<owner>/<name> are both in use.
    in_repos = _child_directories(root / REPOS_DIRNAME, budget)
    yield from in_repos
    for directory in in_repos:
        yield from _child_directories(directory, budget)
    yield from _child_directories(root.joinpath(*REPOSITORY_CACHE_PARTS), budget)
    yield from _child_directories(root, budget)


def _child_directories(parent: Path, budget: _Budget) -> list[Path]:
    """The directories directly inside ``parent``, by name; none when it is missing."""

    budget.check()
    try:
        with os.scandir(parent) as entries:
            names = sorted(entry.name for entry in entries if _is_directory(entry))
    except OSError:
        return []
    return [parent / name for name in names]


def _is_directory(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def _describe_repository(
    relative_path: str,
    directory: Path,
    budget: _Budget,
    git_timeout_seconds: float,
) -> dict[str, Any] | None:
    """One entry of the listing, or None when git does not read the directory.

    A field whose command fails or times out keeps its "unknown" value ("" or
    None) and the repository is still listed: a huge work tree whose status
    times out is exactly the one a member wants to see in the picker.
    """

    environment = _git_environment(directory)

    def git(*args: str) -> tuple[int | None, str]:
        return _run_git(args, directory, environment, budget, git_timeout_seconds)

    # Exit 0 names the branch and exit 1 is a detached HEAD; anything else
    # means this is not a repository git can read.
    code, branch = git("symbolic-ref", "--short", "-q", "HEAD")
    if code not in (0, 1):
        return None
    branch = branch if code == 0 else ""

    head = ""
    last_commit_at: str | None = None
    # Plumbing, so no log.* setting of the member changes the output. It fails
    # on a branch with no commit yet, which leaves both fields unknown.
    code, output = git("rev-list", "-1", "--timestamp", "--abbrev-commit", "HEAD")
    if code == 0:
        timestamp, _, head = output.partition(" ")
        last_commit_at = _iso_utc(timestamp)

    # The URL as stored, not `git remote get-url`, which applies the
    # url.*.insteadOf rewrites: the listing names the remote the repository
    # was cloned from, not what a rewrite (often one carrying a token) makes
    # of it.
    code, output = git("config", "--get", "remote.origin.url")
    remote_url, host, repo = parse_remote(output if code == 0 else "")

    upstream = ""
    if branch:
        code, output = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        upstream = output if code == 0 else ""

    default_branch = ""
    if remote_url:
        code, output = git("symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
        if code == 0 and output.startswith("origin/"):
            default_branch = output[len("origin/"):]

    ahead: int | None = None
    base = upstream or (f"origin/{default_branch}" if default_branch else "")
    if base and head:
        code, output = git("rev-list", "--count", f"{base}..HEAD", "--")
        if code == 0 and output.isdigit():
            ahead = int(output)

    dirty: bool | None = None
    code, output = git("status", "--porcelain", "--untracked-files=no")
    if code == 0:
        dirty = bool(output)

    return {
        "path": relative_path,
        "name": relative_path.rpartition("/")[2],
        "remote_url": remote_url,
        "host": host,
        "repo": repo,
        "branch": branch,
        "head": head,
        "upstream": upstream,
        "default_branch": default_branch,
        "ahead": ahead,
        "dirty": dirty,
        "last_commit_at": last_commit_at,
    }


def _git_environment(directory: Path) -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if name.upper() not in _REPOSITORY_ENVIRONMENT
    }
    # Never wait on a prompt nobody will answer, and never take the index
    # lock: `git status` otherwise rewrites the index to refresh it, and this
    # endpoint must not write into a work tree the agent may be using.
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    # A directory with a broken `.git` would otherwise be answered for by
    # whatever repository encloses it, the workspace root for one.
    environment["GIT_CEILING_DIRECTORIES"] = str(directory.parent)
    return environment


def _run_git(
    args: tuple[str, ...],
    directory: Path,
    environment: dict[str, str],
    budget: _Budget,
    git_timeout_seconds: float,
) -> tuple[int | None, str]:
    """(exit code, stripped stdout) of one git command; the code is None when it did not finish.

    Raises ``_BudgetSpent`` when the scan's budget is gone, before starting or
    because the command was cut short by it, so the caller stops instead of
    reporting a repository it only half read.
    """

    remaining = budget.remaining()
    if remaining <= 0:
        raise _BudgetSpent()
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(directory),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=min(git_timeout_seconds, remaining),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        budget.check()
        return None, ""
    except OSError:
        # No git executable, or a directory that vanished meanwhile.
        return None, ""
    return completed.returncode, completed.stdout.strip()


def _iso_utc(timestamp: str) -> str | None:
    try:
        return datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "MAX_REPOS",
    "TOTAL_BUDGET_SECONDS",
    "list_workspace_repos",
    "parse_remote",
]
