from pathlib import Path


def test_dockerfile_uses_ubuntu_base_and_installs_python_311_runtime():
    text = Path("Dockerfile").read_text(encoding="utf-8")
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
    text = Path("Dockerfile").read_text(encoding="utf-8")
    assert "/app/skills" in text
    assert "/app/tools" not in text
    assert "/root/.efp/workspace" in text
    assert "/root/.efp/skills" in text
    assert "EXPOSE 8000" in text
    assert 'CMD ["python", "main.py"]' in text


def test_dockerfile_installs_gh_and_copies_runtime_tools_binaries():
    text = Path("Dockerfile").read_text(encoding="utf-8")
    assert "https://cli.github.com/packages" in text
    assert "githubcli-archive-keyring.gpg" in text
    assert " gh \\" in text or "\n        gh\n" in text
    assert "COPY runtime-tools/jira runtime-tools/confluence /usr/local/bin/" in text
    assert "chmod 0755 /usr/local/bin/jira /usr/local/bin/confluence" in text
    assert "Go toolchain" in text
