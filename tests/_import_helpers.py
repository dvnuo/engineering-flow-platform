"""Helpers for import-light test module loading from repository paths."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _ensure_package(package_name: str) -> None:
    if package_name in sys.modules:
        return
    module = types.ModuleType(package_name)
    module.__path__ = []  # type: ignore[attr-defined]
    sys.modules[package_name] = module


def load_module_from_repo_path(module_name: str, relative_path: str):
    """Load and execute a module from ``relative_path`` under repo root.

    Supports package-qualified names (for example ``src.gateway.events``) so
    relative imports inside the loaded module continue to resolve.
    """
    repo_root = Path(__file__).resolve().parent.parent
    module_path = repo_root / relative_path

    package_parts = module_name.split(".")[:-1]
    for idx in range(1, len(package_parts) + 1):
        _ensure_package(".".join(package_parts[:idx]))

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to create module spec for {module_name} at {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
