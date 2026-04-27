from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def load_requirement_bundle_assets_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    runtime_pkg = types.ModuleType("src.runtime")
    runtime_pkg.__path__ = []

    github_api_mod = types.ModuleType("src.github.api")
    github_api_mod.github_channel = types.SimpleNamespace(
        get_file=None,
        create_or_update_file=None,
    )

    context_blob_mod = types.ModuleType("src.context_blob_store")
    context_blob_mod.read_ref = lambda *args, **kwargs: ""
    context_blob_mod.put_text = lambda **kwargs: "ctx://context/s/k/sha"

    modules = {
        "src": src_pkg,
        "src.runtime": runtime_pkg,
        "src.github.api": github_api_mod,
        "src.context_blob_store": context_blob_mod,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    src_pkg.runtime = runtime_pkg

    spec = importlib.util.spec_from_file_location(
        "src.runtime.requirement_bundle_assets",
        Path("src/runtime/requirement_bundle_assets.py"),
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["src.runtime.requirement_bundle_assets"] = module
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        sys.modules.pop("src.runtime.requirement_bundle_assets", None)

    return module, _cleanup
