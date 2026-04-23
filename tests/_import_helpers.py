"""Helpers for import-light test module loading from repository paths."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _attach_lazy_package_init(module: types.ModuleType, package_name: str, package_dir: Path) -> None:
    init_file = package_dir / "__init__.py"
    if not init_file.is_file():
        return
    if module.__dict__.get("__lazy_init_attached__", False):
        return

    def _load_init_once() -> None:
        if module.__dict__.get("__lazy_init_loaded__", False):
            return
        spec = importlib.util.spec_from_file_location(package_name, init_file)
        if spec is None or spec.loader is None:
            return
        module.__lazy_init_loaded__ = True
        module.__file__ = str(init_file)
        module.__package__ = package_name
        module.__spec__ = spec
        spec.loader.exec_module(module)

    def __getattr__(name: str):
        _load_init_once()
        if name in module.__dict__:
            return module.__dict__[name]
        raise AttributeError(name)

    module.__getattr__ = __getattr__  # type: ignore[attr-defined]
    module.__lazy_init_attached__ = True


def _ensure_package(package_name: str, repo_root: Path) -> None:
    module = sys.modules.get(package_name)

    package_parts = package_name.split(".")
    package_dir = repo_root.joinpath(*package_parts)
    package_path = str(package_dir) if package_dir.is_dir() else None

    if module is None:
        module = types.ModuleType(package_name)
        module.__path__ = [package_path] if package_path else []  # type: ignore[attr-defined]
        if package_path:
            _attach_lazy_package_init(module, package_name, Path(package_path))
        sys.modules[package_name] = module
        return

    current_path = getattr(module, "__path__", None)
    if package_path is not None and current_path is not None:
        if package_path not in list(current_path):
            current_path.append(package_path)
        _attach_lazy_package_init(module, package_name, Path(package_path))


def load_module_from_repo_path(module_name: str, relative_path: str):
    """Load and execute a module from ``relative_path`` under repo root.

    Supports package-qualified names such as ``src.gateway.events`` so relative
    imports inside the loaded module continue to resolve. The helper creates
    lightweight package modules with real ``__path__`` values so later standard
    imports such as ``src.gateway.server`` keep working during full test
    collection.
    """
    repo_root = Path(__file__).resolve().parent.parent
    module_path = repo_root / relative_path

    package_parts = module_name.split(".")[:-1]
    for idx in range(1, len(package_parts) + 1):
        _ensure_package(".".join(package_parts[:idx]), repo_root)

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to create module spec for {module_name} at {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
