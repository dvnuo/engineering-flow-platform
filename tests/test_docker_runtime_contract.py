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
    assert "ENV PATH=\"/opt/venv/bin:$PATH\"" in text
    assert "pip install --no-cache-dir -r requirements.txt" in text


def test_dockerfile_keeps_native_runtime_asset_dirs_and_port():
    text = _text("Dockerfile")
    assert "/app/skills" in text
    assert "/app/tools" not in text
    assert "/root/.efp/workspace" in text
    assert "/root/.efp/skills" in text
    assert "EXPOSE 8000" in text
    assert 'CMD ["python", "main.py"]' in text


def test_dockerfile_installs_gh_and_copies_runtime_tools_binaries():
    text = _text("Dockerfile")
    assert "https://cli.github.com/packages" in text
    assert "githubcli-archive-keyring.gpg" in text
    assert " gh \\" in text or "\n        gh\n" in text
    assert "COPY runtime-tools/jira runtime-tools/confluence /usr/local/bin/" in text
    assert "chmod 0755 /usr/local/bin/jira /usr/local/bin/confluence" in text
    assert "Go toolchain" in text


def test_prepare_runtime_tools_script_builds_jira_and_confluence_only():
    text = _text("scripts/prepare-runtime-tools.sh")
    assert "set -euo pipefail" in text
    assert "EFP_TOOLS_REPO_DIR" in text
    assert "go build" in text
    assert "./cmd/jira" in text
    assert "./cmd/confluence" in text
    assert "runtime-tools/jira" in text
    assert "runtime-tools/confluence" in text
    assert "./cmd/browser" not in text


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
