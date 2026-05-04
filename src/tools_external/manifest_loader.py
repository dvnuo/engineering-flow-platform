from __future__ import annotations

import logging
import os
import json
from pathlib import Path
from typing import Any, Optional

from .contracts import ToolDescriptor, descriptor_from_mapping, is_descriptor_native_compatible

logger = logging.getLogger(__name__)


def resolve_external_tools_dir() -> Optional[Path]:
    env_value = os.getenv("EFP_TOOLS_DIR")
    if env_value is not None and env_value.strip():
        path = Path(env_value.strip())
        if path.exists():
            return path
        logger.debug("External tools directory does not exist: %s", path)
        return None

    app_tools = Path("/app/tools")
    if app_tools.exists():
        return app_tools

    repo_root = Path(__file__).resolve().parents[2]
    fixture = repo_root / "tools" / "fixture"
    if fixture.exists():
        return fixture
    return None


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
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ="safe")
        return yaml.load(text)
    except Exception:
        return json.loads(text)


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


def load_tool_descriptors(tools_dir: Optional[Path] = None) -> list[ToolDescriptor]:
    resolved_dir = tools_dir or resolve_external_tools_dir()
    if resolved_dir is None:
        return []
    if not resolved_dir.exists():
        logger.debug("External tools directory does not exist: %s", resolved_dir)
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
            if not is_descriptor_native_compatible(descriptor):
                continue
            descriptors.append(descriptor)

    return descriptors
