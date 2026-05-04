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
    assert "/app/tools" in text
    assert "/root/.efp/workspace" in text
    assert "/root/.efp/skills" in text
    assert "EXPOSE 8000" in text
    assert 'CMD ["python", "main.py"]' in text
