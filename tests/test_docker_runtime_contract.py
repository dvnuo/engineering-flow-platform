from pathlib import Path


def test_dockerfile_uses_python_311_runtime():
    text = Path("Dockerfile").read_text(encoding="utf-8")
    first_from = next(line.strip() for line in text.splitlines() if line.strip().startswith("FROM "))
    assert "python:3.11" in first_from
    assert "python:3.9" not in first_from


def test_dockerfile_keeps_native_runtime_asset_dirs_and_port():
    text = Path("Dockerfile").read_text(encoding="utf-8")
    assert "/app/skills" in text
    assert "/app/tools" in text
    assert "/root/.efp/workspace" in text
    assert "EXPOSE 8000" in text
