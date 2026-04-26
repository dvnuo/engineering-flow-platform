from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def load_file_parser_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []

    modules = {
        "src": src_pkg,
        "src.utils": utils_pkg,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    src_pkg.utils = utils_pkg

    module_path = Path("src/utils/file_parser/__init__.py")
    spec = importlib.util.spec_from_file_location(
        "src.utils.file_parser",
        module_path,
        submodule_search_locations=[str(module_path.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["src.utils.file_parser"] = module
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        for name in list(sys.modules.keys()):
            if name == "src.utils.file_parser" or name.startswith("src.utils.file_parser."):
                sys.modules.pop(name, None)

    return module, _cleanup
