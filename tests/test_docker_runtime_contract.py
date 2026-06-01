import os
import shutil
import stat
import subprocess
from pathlib import Path


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_dockerfile_uses_ubuntu_base_and_installs_python_311_runtime():
    text = _text("Dockerfile")
    first_from = next(line.strip() for line in text.splitlines() if line.strip().startswith("FROM "))

    assert first_from == "FROM ubuntu:22.04"
    assert "python:" not in first_from
    assert "python:3.11" not in text
    assert "python:3.9" not in text

    assert "python3.11" in text
    assert "python3.11-venv" in text
    assert "VIRTUAL_ENV=/opt/venv" in text
    assert "ENV PATH=\"/opt/venv/bin:/usr/local/bin:$PATH\"" in text
    assert "pip install --no-cache-dir -r requirements.txt" in text


def test_dockerfile_keeps_native_runtime_asset_dirs_and_port():
    text = _text("Dockerfile")
    assert "/app/skills" in text
    assert "/app/tools" not in text
    assert "/workspace" in text
    assert "/root/.efp/" + "workspace" not in text
    assert "/root/.efp/skills" not in text
    assert "EXPOSE 8000" in text
    assert 'CMD ["python", "main.py"]' in text


def test_dockerfile_installs_gh_and_copies_runtime_tools_binaries():
    text = _text("Dockerfile")
    assert "https://cli.github.com/packages" in text
    assert "githubcli-archive-keyring.gpg" in text
    assert " gh \\" in text or "\n        gh\n" in text
    assert "google-chrome-stable" in text
    assert "google-chrome --version" in text
    assert "COPY runtime-tools/ /tmp/runtime-tools/" in text
    assert "find /tmp/runtime-tools" in text
    assert "install -m 0755" in text
    assert "COPY runtime-tools/jira runtime-tools/confluence /usr/local/bin/" not in text
    assert "chmod 0755 /usr/local/bin/jira /usr/local/bin/confluence" not in text
    assert "Go toolchain" in text
    for command in [
        "jira version --json",
        "jira commands --json",
        "jira schema issue.map-csv --json",
        "confluence version --json",
        "confluence commands --json",
        "confluence schema page.create --json",
        "browser version --json",
        "browser commands --json",
        "browser schema probe --json",
    ]:
        assert command in text


def test_prepare_runtime_tools_script_discovers_all_cmd_tools(tmp_path):
    project_root = tmp_path / "runtime"
    script_dir = project_root / "scripts"
    script_dir.mkdir(parents=True)
    runtime_tools = project_root / "runtime-tools"
    runtime_tools.mkdir()
    (runtime_tools / "README.md").write_text("kept\n", encoding="utf-8")
    script_path = script_dir / "prepare-runtime-tools.sh"
    shutil.copy2("scripts/prepare-runtime-tools.sh", script_path)
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

    tools_repo = tmp_path / "engineering-flow-platform-tools"
    for tool_name in ["jira", "confluence", "browser"]:
        command_dir = tools_repo / "cmd" / tool_name
        command_dir.mkdir(parents=True)
        (command_dir / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tools_repo / "go.mod").write_text("module example.test/tools\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_go_log = tmp_path / "go-builds.log"
    fake_go = fake_bin / "go"
    fake_go.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != "build" ]]; then
  exit 64
fi
out=""
pkg=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -o)
      out="$2"
      shift 2
      ;;
    *)
      pkg="$1"
      shift
      ;;
  esac
done
tool="${pkg##*/}"
printf '%s %s %s %s\\n' "$tool" "${GOOS:-}" "${GOARCH:-}" "${CGO_ENABLED:-}" >> "$EFP_FAKE_GO_LOG"
mkdir -p "$(dirname "$out")"
printf '#!/usr/bin/env bash\\necho "%s"\\n' "$tool" > "$out"
""",
        encoding="utf-8",
    )
    fake_go.chmod(fake_go.stat().st_mode | stat.S_IXUSR)

    env = dict(os.environ)
    env["EFP_TOOLS_REPO_DIR"] = str(tools_repo)
    env["EFP_FAKE_GO_LOG"] = str(fake_go_log)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env.pop("GOOS", None)
    env.pop("GOARCH", None)
    env.pop("CGO_ENABLED", None)

    result = subprocess.run(
        [str(script_path)],
        cwd=project_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Discovered runtime tools: browser confluence jira" in result.stderr
    assert "Built runtime tools: browser confluence jira" in result.stderr
    assert (runtime_tools / "README.md").read_text(encoding="utf-8") == "kept\n"
    for tool_name in ["jira", "confluence", "browser"]:
        tool_path = runtime_tools / tool_name
        assert tool_path.exists()
        assert tool_path.stat().st_mode & 0o111
    assert fake_go_log.read_text(encoding="utf-8").splitlines() == [
        "browser linux amd64 0",
        "confluence linux amd64 0",
        "jira linux amd64 0",
    ]


def test_prepare_runtime_tools_script_uses_dynamic_cmd_discovery():
    text = _text("scripts/prepare-runtime-tools.sh")
    assert "set -euo pipefail" in text
    assert "EFP_TOOLS_REPO_DIR" in text
    assert "GOOS:-linux" in text
    assert "GOARCH:-amd64" in text
    assert "find \"$TOOLS_REPO_DIR/cmd\"" in text
    assert "go build" in text
    assert "\"./cmd/$tool_name\"" in text
    assert "./cmd/jira" not in text
    assert "./cmd/confluence" not in text
    assert "./cmd/browser" not in text


def test_runtime_tools_gitignore_and_readme_cover_dynamic_binaries():
    gitignore = _text(".gitignore")
    assert "runtime-tools/*" in gitignore
    assert "!runtime-tools/README.md" in gitignore
    readme = _text("runtime-tools/README.md")
    assert "cmd/<tool>/main.go" in readme
    assert "jira" in readme
    assert "confluence" in readme
    assert "browser" in readme


def test_workflows_prepare_runtime_tools_before_docker_builds():
    workflow_expectations = [
        (".github/workflows/ci.yml", "docker build -t efp-native-runtime-ci ."),
        (".github/workflows/docker-image.yml", "docker/build-push-action@v6"),
    ]

    for workflow_path, docker_build_marker in workflow_expectations:
        text = _text(workflow_path)
        prepare_index = text.index("scripts/prepare-runtime-tools.sh")
        docker_build_index = text.index(docker_build_marker)

        assert prepare_index < docker_build_index
        assert "dvnuo/engineering-flow-platform-tools" in text
        assert "actions/setup-go@v5" in text
        assert "go-version-file: engineering-flow-platform-tools/go.mod" in text
        assert (
            "EFP_TOOLS_REPO_DIR=engineering-flow-platform-tools "
            "scripts/prepare-runtime-tools.sh"
        ) in text
