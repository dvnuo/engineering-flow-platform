"""Tests for the listing of git repositories cloned in the workspace.

The repositories are real ones made with git in a temporary directory: what is
under test is mostly which git command answers which field, and a stand-in for
git would only repeat the assumptions the module already makes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest

from src.gateway import workspace_repos
from src.gateway.workspace_repos import (
    GIT_TIMEOUT_SECONDS,
    MAX_REPOS,
    TOTAL_BUDGET_SECONDS,
    list_workspace_repos,
    parse_remote,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

ENTRY_KEYS = [
    "path",
    "name",
    "remote_url",
    "host",
    "repo",
    "branch",
    "head",
    "upstream",
    "default_branch",
    "ahead",
    "dirty",
    "last_commit_at",
]

READ_ONLY_GIT_COMMANDS = {"symbolic-ref", "rev-list", "config", "rev-parse", "status"}


def _git(cwd: Path, *args: str, when: str | None = None) -> str:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if when is not None:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _commit(repo: Path, message: str, *, when: str = "2026-11-02T00:00:00+00:00") -> None:
    _git(repo, "commit", "-q", "--allow-empty", "-m", message, when=when)


@pytest.fixture(scope="module")
def template_repo(tmp_path_factory) -> Path:
    """One repository on `main` with one commit; the tests copy it.

    Everything a commit needs is set in the repository itself, so neither the
    developer's nor the build machine's git configuration decides the outcome.
    """

    repo = tmp_path_factory.mktemp("template") / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.name", "Workspace Test")
    _git(repo, "config", "user.email", "workspace-test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "README.md").write_text("template\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "first", when="2026-11-01T00:00:00+00:00")
    return repo


@pytest.fixture
def make_repo(template_repo: Path, tmp_path: Path):
    def make(relative: str, *, root: Path | None = None) -> Path:
        target = (root or tmp_path / "workspace") / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template_repo, target)
        return target

    return make


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def _scan(root: Path, **overrides) -> dict:
    # The budget is not what most tests are about, and a slow machine must not
    # turn them into tests of it.
    options = {"total_budget_seconds": 120.0, "git_timeout_seconds": 60.0, **overrides}
    return list_workspace_repos(root, **options)


def _by_path(payload: dict) -> dict[str, dict]:
    return {entry["path"]: entry for entry in payload["repos"]}


def _link_directory(target: Path, link: Path) -> None:
    """Make ``link`` point at ``target``: a symlink, or a junction where Windows refuses one."""

    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        # A junction needs no privilege, and it resolves like a symlink.
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError):
        pytest.skip("this account can create neither a symbolic link nor a junction")


def test_the_limits_are_the_contract_values():
    assert (MAX_REPOS, GIT_TIMEOUT_SECONDS, TOTAL_BUDGET_SECONDS) == (30, 3.0, 8.0)


def test_a_workspace_without_repositories_lists_nothing(workspace, tmp_path):
    (workspace / "repos").mkdir()
    (workspace / "notes").mkdir()

    assert _scan(workspace) == {"repos": [], "truncated": False}
    assert _scan(tmp_path / "missing") == {"repos": [], "truncated": False}


def test_repositories_are_found_in_the_fixed_places_only(workspace, make_repo):
    make_repo("repos/efp-portal")
    make_repo("repos/org/efp-tools")
    make_repo(".efp_runtime/repositories/cached-1a2b3c")
    make_repo("direct-child")
    # Real repositories in places the scan does not look.
    make_repo("repos/too/deep/repo")
    make_repo("elsewhere/nested")
    # And directories that are not repositories.
    (workspace / "repos" / "not-a-repo").mkdir()
    (workspace / "repos" / "not-a-repo" / "file.txt").write_text("x", encoding="utf-8")
    (workspace / "plain").mkdir()
    (workspace / "a-file.txt").write_text("x", encoding="utf-8")

    payload = _scan(workspace)

    assert payload["truncated"] is False
    assert {entry["path"]: entry["name"] for entry in payload["repos"]} == {
        "repos/efp-portal": "efp-portal",
        "repos/org/efp-tools": "efp-tools",
        ".efp_runtime/repositories/cached-1a2b3c": "cached-1a2b3c",
        "direct-child": "direct-child",
    }


def test_an_entry_describes_the_branch_the_head_and_the_last_commit(workspace, make_repo):
    repo = make_repo("repos/efp-portal")
    _git(repo, "checkout", "-q", "-b", "feat/x")
    # Committed at +08:00; the listing speaks UTC so its entries sort as text.
    _commit(repo, "work", when="2026-11-12T16:10:00+08:00")

    payload = _scan(workspace)

    assert payload == {
        "repos": [
            {
                "path": "repos/efp-portal",
                "name": "efp-portal",
                "remote_url": "",
                "host": "",
                "repo": "",
                "branch": "feat/x",
                "head": _git(repo, "rev-parse", "--short", "HEAD"),
                "upstream": "",
                "default_branch": "",
                "ahead": None,
                "dirty": False,
                "last_commit_at": "2026-11-12T08:10:00+00:00",
            }
        ],
        "truncated": False,
    }
    assert list(payload["repos"][0]) == ENTRY_KEYS
    json.dumps(payload)


def test_a_detached_head_has_no_branch(workspace, make_repo):
    repo = make_repo("repos/detached")
    _git(repo, "checkout", "-q", "--detach")

    entry = _by_path(_scan(workspace))["repos/detached"]

    assert entry["branch"] == ""
    assert entry["upstream"] == ""
    assert entry["head"] == _git(repo, "rev-parse", "--short", "HEAD")
    assert entry["last_commit_at"] == "2026-11-01T00:00:00+00:00"


def test_a_repository_without_a_commit_is_listed_last_with_what_is_known(workspace, make_repo):
    make_repo("repos/has-commits")
    unborn = workspace / "repos" / "a-unborn"
    unborn.mkdir()
    _git(unborn, "init", "-q")
    _git(unborn, "symbolic-ref", "HEAD", "refs/heads/main")

    payload = _scan(workspace)

    assert [entry["path"] for entry in payload["repos"]] == ["repos/has-commits", "repos/a-unborn"]
    assert payload["repos"][1]["branch"] == "main"
    assert payload["repos"][1]["head"] == ""
    assert payload["repos"][1]["last_commit_at"] is None
    assert payload["repos"][1]["ahead"] is None


def test_credentials_in_the_origin_url_never_leave_the_runtime(workspace, make_repo):
    repo = make_repo("repos/efp-portal")
    _git(repo, "remote", "add", "origin", "https://x-access-token:s3cr3t-t0ken@github.com/Org/efp-portal.git")

    payload = _scan(workspace)
    entry = payload["repos"][0]

    assert entry["remote_url"] == "https://github.com/Org/efp-portal.git"
    assert entry["host"] == "github.com"
    assert entry["repo"] == "Org/efp-portal"
    assert "s3cr3t-t0ken" not in json.dumps(payload)
    assert "x-access-token" not in json.dumps(payload)
    # Nothing was fetched, so the remote has no branches here yet.
    assert entry["default_branch"] == ""
    assert entry["ahead"] is None


def test_an_scp_like_origin_is_parsed_to_host_and_repo(workspace, make_repo):
    repo = make_repo("repos/efp-tools")
    _git(repo, "remote", "add", "origin", "git@github.com:org/efp-tools.git")

    entry = _scan(workspace)["repos"][0]

    assert entry["remote_url"] == "github.com:org/efp-tools.git"
    assert entry["host"] == "github.com"
    assert entry["repo"] == "org/efp-tools"


def test_ahead_counts_against_the_upstream_and_else_the_default_branch(workspace, make_repo, tmp_path):
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "-q", "--bare")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    repo = make_repo("repos/app")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "remote", "set-head", "origin", "main")

    pushed = _by_path(_scan(workspace))["repos/app"]

    assert pushed["branch"] == "main"
    assert pushed["upstream"] == "origin/main"
    assert pushed["default_branch"] == "main"
    assert pushed["ahead"] == 0
    # A path on disk names no host a picker could ask.
    assert pushed["remote_url"] == str(origin)
    assert (pushed["host"], pushed["repo"]) == ("", "")

    _commit(repo, "second", when="2026-11-03T00:00:00+00:00")
    _commit(repo, "third", when="2026-11-04T00:00:00+00:00")

    assert _by_path(_scan(workspace))["repos/app"]["ahead"] == 2

    # A branch that tracks nothing is measured against origin's default branch.
    _git(repo, "checkout", "-q", "-b", "feat/y")
    _commit(repo, "fourth", when="2026-11-05T00:00:00+00:00")
    feature = _by_path(_scan(workspace))["repos/app"]

    assert feature["branch"] == "feat/y"
    assert feature["upstream"] == ""
    assert feature["default_branch"] == "main"
    assert feature["ahead"] == 3

    # Detached, there is still a default branch to measure against.
    _git(repo, "checkout", "-q", "--detach", "HEAD~1")
    detached = _by_path(_scan(workspace))["repos/app"]

    assert (detached["branch"], detached["upstream"], detached["ahead"]) == ("", "", 2)


def test_dirty_means_a_tracked_file_changed(workspace, make_repo):
    clean = make_repo("repos/clean")
    untracked = make_repo("repos/untracked")
    modified = make_repo("repos/modified")
    staged = make_repo("repos/staged")
    (untracked / "scratch.txt").write_text("not tracked\n", encoding="utf-8")
    (modified / "README.md").write_text("changed\n", encoding="utf-8")
    (staged / "README.md").write_text("changed and staged\n", encoding="utf-8")
    _git(staged, "add", "README.md")

    entries = _by_path(_scan(workspace))

    assert entries["repos/clean"]["dirty"] is False
    assert entries["repos/untracked"]["dirty"] is False
    assert entries["repos/modified"]["dirty"] is True
    assert entries["repos/staged"]["dirty"] is True
    # Looking must not take the index lock or rewrite the index.
    assert clean.joinpath(".git", "index.lock").exists() is False


def test_the_newest_commit_comes_first_and_the_cap_keeps_the_newest(workspace, make_repo):
    # Scan order is repos/a, repos/b, direct; by date it is b, direct, a.
    _commit(make_repo("repos/a"), "old", when="2026-11-02T00:00:00+00:00")
    _commit(make_repo("repos/b"), "new", when="2026-11-09T00:00:00+00:00")
    _commit(make_repo("direct"), "middle", when="2026-11-05T12:00:00+05:00")

    everything = _scan(workspace)
    capped = _scan(workspace, max_repos=2)
    exact = _scan(workspace, max_repos=3)

    assert [entry["path"] for entry in everything["repos"]] == ["repos/b", "direct", "repos/a"]
    assert everything["truncated"] is False
    assert [entry["path"] for entry in capped["repos"]] == ["repos/b", "direct"]
    assert capped["truncated"] is True
    assert exact == everything


def test_a_directory_with_a_broken_git_entry_is_not_answered_for_by_its_parent(workspace, template_repo):
    # The workspace root is itself a repository here (the agents repo often
    # is). Git, left alone, would walk up from the broken directory and
    # describe the root in its name.
    shutil.copytree(template_repo, workspace, dirs_exist_ok=True)
    (workspace / "empty-git-dir" / ".git").mkdir(parents=True)
    (workspace / "garbage-git-file").mkdir()
    (workspace / "garbage-git-file" / ".git").write_text("not a gitfile\n", encoding="utf-8")
    shutil.copytree(template_repo, workspace / "real")

    payload = _scan(workspace)

    # The root is never listed, whatever it is.
    assert [entry["path"] for entry in payload["repos"]] == ["real"]
    assert payload["truncated"] is False


def test_a_link_out_of_the_workspace_is_skipped_and_a_link_inside_is_listed_once(workspace, make_repo, tmp_path):
    outside = make_repo("outside-repo", root=tmp_path / "elsewhere")
    inside = make_repo("direct")
    (workspace / "repos").mkdir()
    _link_directory(outside, workspace / "repos" / "escape")
    _link_directory(inside, workspace / "repos" / "alias")

    payload = _scan(workspace)

    # `repos/` is scanned before the root's children, so the alias names it.
    assert [entry["path"] for entry in payload["repos"]] == ["repos/alias"]


def test_every_git_command_is_local_read_only_and_bounded(workspace, make_repo, monkeypatch):
    repo = make_repo("repos/app")
    _git(repo, "remote", "add", "origin", "https://github.com/org/app.git")
    monkeypatch.setenv("GIT_DIR", str(workspace / "somewhere-else"))
    monkeypatch.setenv("GIT_WORK_TREE", str(workspace))
    calls: list[tuple[list[str], dict]] = []
    real_run = subprocess.run

    def recording_run(command, **kwargs):
        calls.append((list(command), kwargs))
        return real_run(command, **kwargs)

    monkeypatch.setattr(workspace_repos.subprocess, "run", recording_run)

    payload = list_workspace_repos(workspace)

    assert [entry["path"] for entry in payload["repos"]] == ["repos/app"]
    assert calls
    for command, kwargs in calls:
        assert command[0] == "git"
        assert command[1] in READ_ONLY_GIT_COMMANDS
        assert 0 < kwargs["timeout"] <= GIT_TIMEOUT_SECONDS
        assert Path(kwargs["cwd"]) == repo.resolve()
        assert kwargs["stdin"] == subprocess.DEVNULL
        env = kwargs["env"]
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_OPTIONAL_LOCKS"] == "0"
        assert Path(env["GIT_CEILING_DIRECTORIES"]) == repo.resolve().parent
        # Inherited, these would aim every command at one other repository.
        assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    assert {command[1] for command, _ in calls} == READ_ONLY_GIT_COMMANDS


def test_a_command_that_times_out_costs_only_its_field(workspace, make_repo, monkeypatch):
    make_repo("repos/huge")
    real_run = subprocess.run

    def slow_status(command, **kwargs):
        if command[1] == "status":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return real_run(command, **kwargs)

    monkeypatch.setattr(workspace_repos.subprocess, "run", slow_status)

    payload = _scan(workspace)

    assert payload["truncated"] is False
    assert payload["repos"][0]["path"] == "repos/huge"
    assert payload["repos"][0]["branch"] == "main"
    assert payload["repos"][0]["dirty"] is None


def test_when_the_budget_runs_out_what_is_collected_is_returned_as_truncated(workspace, make_repo, monkeypatch):
    make_repo("repos/a")
    make_repo("repos/b")
    clock = SimpleNamespace(now=1000.0)
    real_run = subprocess.run

    def run_and_let_b_take_forever(command, **kwargs):
        if Path(kwargs["cwd"]).name == "b":
            clock.now += 60.0
        return real_run(command, **kwargs)

    monkeypatch.setattr(workspace_repos, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(workspace_repos.subprocess, "run", run_and_let_b_take_forever)

    payload = list_workspace_repos(workspace)

    # `b` was only half read when time ran out, so it is not reported at all.
    assert [entry["path"] for entry in payload["repos"]] == ["repos/a"]
    assert payload["truncated"] is True


def test_no_budget_at_all_still_answers(workspace, make_repo):
    make_repo("repos/a")

    assert list_workspace_repos(workspace, total_budget_seconds=0) == {"repos": [], "truncated": True}


def test_a_missing_git_executable_lists_nothing_rather_than_failing(workspace, make_repo, monkeypatch):
    make_repo("repos/a")

    def no_git(command, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(workspace_repos.subprocess, "run", no_git)

    assert _scan(workspace) == {"repos": [], "truncated": False}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://github.com/org/repo.git", ("https://github.com/org/repo.git", "github.com", "org/repo")),
        ("https://user:tok%40en@GitHub.com/org/repo", ("https://GitHub.com/org/repo", "github.com", "org/repo")),
        ("https://user:p@ss@github.com/org/repo.git", ("https://github.com/org/repo.git", "github.com", "org/repo")),
        (
            "https://x-access-token:ghp_abc@ghe.example.com:8443/team/sub/repo.git/",
            ("https://ghe.example.com:8443/team/sub/repo.git/", "ghe.example.com", "team/sub/repo"),
        ),
        (
            "https://user@host.example/a/b.git?x=1#frag",
            ("https://host.example/a/b.git?x=1#frag", "host.example", "a/b"),
        ),
        ("ssh://git@github.com:22/org/repo.git", ("ssh://github.com:22/org/repo.git", "github.com", "org/repo")),
        (
            "ssh://git@[2001:db8::1]:2222/org/repo.git",
            ("ssh://[2001:db8::1]:2222/org/repo.git", "2001:db8::1", "org/repo"),
        ),
        ("git@github.com:org/repo.git", ("github.com:org/repo.git", "github.com", "org/repo")),
        ("GitHub.com:org/repo", ("GitHub.com:org/repo", "github.com", "org/repo")),
        ("git@host.example:/srv/git/repo.git", ("host.example:/srv/git/repo.git", "host.example", "srv/git/repo")),
        # Not forms git accepts, but a password written into one must not get out.
        ("user:pass@host.example:org/repo.git", ("host.example:org/repo.git", "host.example", "org/repo")),
        ("user:pass@host.example/org/repo.git", ("host.example/org/repo.git", "", "")),
        # A path on disk is left alone, "@" and all.
        ("C:\\Users\\me@corp\\origin.git", ("C:\\Users\\me@corp\\origin.git", "", "")),
        ("/home/me@corp/origin.git", ("/home/me@corp/origin.git", "", "")),
        ("  https://github.com/org/repo.git\n", ("https://github.com/org/repo.git", "github.com", "org/repo")),
        ("file:///srv/git/repo.git", ("file:///srv/git/repo.git", "", "")),
        ("/srv/git/repo.git", ("/srv/git/repo.git", "", "")),
        ("../origin.git", ("../origin.git", "", "")),
        ("C:\\work\\origin.git", ("C:\\work\\origin.git", "", "")),
        ("C:/work/origin.git", ("C:/work/origin.git", "", "")),
        ("C://work/origin.git", ("C://work/origin.git", "", "")),
        ("", ("", "", "")),
    ],
)
def test_parse_remote(raw, expected):
    assert parse_remote(raw) == expected


def test_the_route_is_registered_next_to_personalization():
    from aiohttp import web

    from src.gateway.runtime_api import setup_runtime_api_routes

    app = web.Application()
    setup_runtime_api_routes(app)
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}

    assert ("GET", "/api/workspace/repos") in routes
    assert ("GET", "/api/personalization") in routes
    # Read-only: there is nothing to post, put or delete here.
    assert {method for method, path in routes if path == "/api/workspace/repos"} <= {"GET", "HEAD"}


@pytest.mark.asyncio
async def test_the_endpoint_lists_the_runtime_workspace_off_the_event_loop(workspace, make_repo, monkeypatch):
    from aiohttp.test_utils import make_mocked_request

    from src.gateway import runtime_api

    make_repo("repos/app")
    scanned: list[tuple[Path, bool]] = []

    def scan(root):
        scanned.append((Path(root), threading.current_thread() is threading.main_thread()))
        return _scan(root)

    monkeypatch.setattr(runtime_api, "resolve_runtime_workspace", lambda _config: workspace)
    monkeypatch.setattr(runtime_api, "list_workspace_repos", scan)

    # A path in the query is not an input: the workspace is the only root.
    request = make_mocked_request("GET", "/api/workspace/repos?path=/etc&root=..")
    response = await runtime_api.api_workspace_repos(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert [entry["path"] for entry in payload["repos"]] == ["repos/app"]
    assert payload["truncated"] is False
    assert scanned == [(workspace, False)]


@pytest.mark.asyncio
async def test_the_endpoint_answers_an_empty_listing_when_the_scan_fails(workspace, monkeypatch):
    from aiohttp.test_utils import make_mocked_request

    from src.gateway import runtime_api

    def broken(_root):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(runtime_api, "resolve_runtime_workspace", lambda _config: workspace)
    monkeypatch.setattr(runtime_api, "list_workspace_repos", broken)

    response = await runtime_api.api_workspace_repos(make_mocked_request("GET", "/api/workspace/repos"))

    assert response.status == 200
    assert json.loads(response.text) == {"repos": [], "truncated": False}


def test_the_runtime_contract_documents_the_endpoint():
    text = Path("docs/runtime_contract.md").read_text(encoding="utf-8")

    for needle in ["`GET /api/workspace/repos`", "`truncated`", "`default_branch`", "`last_commit_at`"]:
        assert needle in text
