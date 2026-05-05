from __future__ import annotations

import logging
import os
import json
from pathlib import Path
from typing import Any

from .contracts import ToolDescriptor, descriptor_from_mapping

logger = logging.getLogger(__name__)


def resolve_external_tools_dir() -> Path:
    env_value = os.getenv("EFP_TOOLS_DIR")
    if env_value is not None and env_value.strip():
        path = Path(env_value.strip())
    else:
        if os.getenv("EFP_EXTERNAL_TOOLS_TEST_FIXTURE", "").lower() == "true":
            fixture = os.getenv("EFP_TOOLS_FIXTURE_DIR")
            if fixture and Path(fixture).exists():
                return Path(fixture)
        path = Path("/app/tools")

    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.debug("External tools directory mkdir failed for %s: %s", path, exc)
    return path


def discover_manifest_files(tools_dir: Path) -> list[Path]:
    files = set()
    for name in ("manifest.yaml", "manifest.yml"):
        candidate = tools_dir / name
        if candidate.exists() and candidate.is_file():
            files.add(candidate)
    for pattern in ("tools/**/*.yaml", "tools/**/*.yml"):
        for candidate in tools_dir.glob(pattern):
            if candidate.is_file():
                files.add(candidate)
    return sorted(files)


def _load_yaml_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        text = fh.read()
    last_exc: Exception | None = None
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ="safe")
        return yaml.load(text)
    except Exception as exc:
        last_exc = exc
    try:
        import yaml
        return yaml.safe_load(text)
    except Exception as exc:
        last_exc = exc
    try:
        return json.loads(text)
    except Exception as exc:
        last_exc = exc
    raise last_exc or ValueError(f"Unable to parse manifest: {path}")


def _iter_descriptor_mappings(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if isinstance(raw.get("tools"), list):
            return raw["tools"]
        if isinstance(raw.get("descriptors"), list):
            return raw["descriptors"]
        if "name" in raw:
            return [raw]
        return []
    return []


def load_tool_descriptors(tools_dir: Path | None = None) -> list[ToolDescriptor]:
    resolved_dir = tools_dir or resolve_external_tools_dir()
    if not resolved_dir.exists() or not resolved_dir.is_dir():
        logger.debug("External tools directory unavailable: %s", resolved_dir)
        return []
    if not os.access(resolved_dir, os.R_OK):
        logger.debug("External tools directory unreadable: %s", resolved_dir)
        return []

    descriptors: list[ToolDescriptor] = []
    for manifest in discover_manifest_files(resolved_dir):
        try:
            raw = _load_yaml_file(manifest)
        except Exception as exc:
            logger.warning("Failed to parse external tool manifest %s: %s", manifest, exc)
            continue

        for item in _iter_descriptor_mappings(raw):
            try:
                descriptor = descriptor_from_mapping(item, source_file=str(manifest))
            except Exception as exc:
                logger.warning("Invalid descriptor in %s: %s", manifest, exc)
                continue
            descriptors.append(descriptor)

    return descriptors
