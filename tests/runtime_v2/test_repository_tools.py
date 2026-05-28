from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import ASK, PermissionDecision, PermissionMetadata
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


class AllowEvaluator:
    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: ToolContext | None = None,
    ) -> PermissionDecision:
        return PermissionDecision.allow()


def test_core_registry_includes_repository_tools(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert {"repo_clone", "repo_overview"}.issubset(set(registry.ids()))
    clone_permission = registry.require("repo_clone").permission
    overview_permission = registry.require("repo_overview").permission
    assert clone_permission.action == ASK
    assert clone_permission.category == "repository"
    assert clone_permission.risk == "medium"
    assert clone_permission.data["subject_arg"] == "repository"
    assert overview_permission.action == ASK
    assert overview_permission.category == "repository"
    assert overview_permission.risk == "low"
    assert overview_permission.data["subject_arg"] == "repository"


@pytest.mark.asyncio
async def test_repo_overview_workspace_path_detects_dependencies_and_git(
    tmp_path: Path,
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "node_modules" / "dep").mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps(
            {
                "main": "dist/index.js",
                "module": "dist/index.mjs",
                "types": "dist/index.d.ts",
                "bin": {"project": "bin/project.js"},
                "exports": {".": {"import": "./src/index.ts"}},
            }
        ),
        encoding="utf-8",
    )
    (project / "package-lock.json").write_text("{}", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (project / "src" / "index.ts").write_text("export {}\n", encoding="utf-8")
    (project / "src" / "main.py").write_text("print('demo')\n", encoding="utf-8")
    (project / "node_modules" / "dep" / "package.json").write_text(
        "{}",
        encoding="utf-8",
    )
    for index in range(210):
        (project / f"file_{index:03d}.txt").write_text("x\n", encoding="utf-8")
    _init_git_repo(project)

    runtime = _runtime(tmp_path)
    result = await runtime.execute(
        ToolCall(
            id="call-overview-path",
            tool_id="repo_overview",
            args={"path": "project", "depth": 6},
        )
    )

    assert result.status == "success"
    assert result.output["path"] == "project"
    assert result.output["repository"] is None
    assert result.output["branch"] == "main"
    assert result.output["head"]
    assert result.output["package_manager"] == "npm"
    assert result.output["ecosystems"] == ["Node.js", "Python"]
    assert result.output["dependency_files"] == [
        "package-lock.json",
        "package.json",
        "pyproject.toml",
    ]
    assert "node_modules/dep/package.json" not in result.output["dependency_files"]
    assert {
        "dist/index.js",
        "dist/index.mjs",
        "dist/index.d.ts",
        "bin/project.js",
        "src/index.ts",
        "src/main.py",
    }.issubset(set(result.output["entrypoints"]))
    assert len(result.output["structure"]) == 200
    assert result.output["truncated"] is True
    assert result.metadata["truncated"] is True
    assert "node_modules/" not in result.output["structure"]


@pytest.mark.asyncio
async def test_repo_clone_local_repository_caches_and_overviews(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text(
        json.dumps({"main": "src/index.ts"}),
        encoding="utf-8",
    )
    (source / "src").mkdir()
    (source / "src" / "index.ts").write_text("export const value = 1\n", encoding="utf-8")
    _init_git_repo(source)
    source_head = _git_output(source, "rev-parse", "HEAD")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = _runtime(workspace)
    repository = str(source)

    cloned = await runtime.execute(
        ToolCall(
            id="call-repo-clone",
            tool_id="repo_clone",
            args={"repository": repository},
        )
    )
    cached = await runtime.execute(
        ToolCall(
            id="call-repo-cached",
            tool_id="repo_clone",
            args={"repository": repository},
        )
    )
    overview = await runtime.execute(
        ToolCall(
            id="call-repo-overview",
            tool_id="repo_overview",
            args={"repository": repository},
        )
    )

    assert cloned.status == "success"
    assert cloned.output["status"] == "cloned"
    assert cloned.output["repository"] == repository
    assert cloned.output["remote"] == repository
    assert cloned.output["local_path"].startswith(".efp_runtime/repositories/")
    assert cloned.output["branch"] == "main"
    assert cloned.output["head"] == source_head
    assert cloned.output["refresh"] is False
    assert (workspace / cloned.output["local_path"] / ".git").is_dir()

    assert cached.status == "success"
    assert cached.output["status"] == "cached"
    assert cached.output["local_path"] == cloned.output["local_path"]
    assert cached.output["head"] == source_head

    assert overview.status == "success"
    assert overview.output["repository"] == repository
    assert overview.output["path"] == cloned.output["local_path"]
    assert overview.output["branch"] == "main"
    assert overview.output["head"] == source_head
    assert overview.output["ecosystems"] == ["Node.js"]
    assert overview.output["dependency_files"] == ["package.json"]
    assert "src/index.ts" in overview.output["entrypoints"]


@pytest.mark.asyncio
async def test_repo_clone_rejects_target_dir_path_traversal(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# source\n", encoding="utf-8")
    _init_git_repo(source)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = _runtime(workspace)

    result = await runtime.execute(
        ToolCall(
            id="call-repo-clone-outside",
            tool_id="repo_clone",
            args={"repository": str(source), "target_dir": "../outside"},
        )
    )

    assert result.status == "error"
    assert "Path escapes workspace root." in result.error


@pytest.mark.asyncio
async def test_repo_overview_missing_cached_repository_errors(tmp_path: Path):
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-repo-overview-missing",
            tool_id="repo_overview",
            args={"repository": "owner/missing"},
        )
    )

    assert result.status == "error"
    assert "Cached repository not found for owner/missing; use repo_clone first." in result.error


def _runtime(workspace_root: Path) -> ToolRuntime:
    return ToolRuntime(
        create_core_tool_registry(workspace_root),
        permission_evaluator=AllowEvaluator(),
    )


def _init_git_repo(path: Path) -> None:
    _git(path, "init")
    _git(path, "checkout", "-B", "main")
    _git(path, "config", "user.email", "runtime@example.com")
    _git(path, "config", "user.name", "Runtime Test")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "initial")


def _git_output(path: Path, *args: str) -> str:
    result = _git(path, *args)
    return result.stdout.strip()


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
