from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def load_github_url_utils_lightweight():
    spec = importlib.util.spec_from_file_location("src.external_cli.github", Path("src/external_cli/github.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _load_module_with_stubs(module_name: str, module_path: Path, modules: dict[str, types.ModuleType]):
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        # Only remove the dynamically loaded module when it wasn't one of the
        # stubbed entries restored above. For load_root_execute_tool_lightweight(),
        # module_name is "src" and "src" is also restored from prev; popping it
        # here would remove the real package and break parent attrs like
        # src.runtime for later tests.
        if module_name not in modules:
            sys.modules.pop(module_name, None)

    return module, _cleanup

def load_jira_workflow_review_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []
    runtime_pkg = types.ModuleType("src.runtime")
    runtime_pkg.__path__ = []

    runtime_adapter_mod = types.ModuleType("src.runtime.runtime_adapter_execution")

    async def execute_adapter_action_via_bus(action_id, kwargs, **meta):
        return {"success": True, "result": {"action_id": action_id, "kwargs": kwargs}, "error": None}

    runtime_adapter_mod.execute_adapter_action_via_bus = execute_adapter_action_via_bus

    contract_spec = importlib.util.spec_from_file_location(
        "src.runtime.jira_workflow_contract",
        Path("src/runtime/jira_workflow_contract.py"),
    )
    contract_mod = importlib.util.module_from_spec(contract_spec)
    assert contract_spec and contract_spec.loader
    sys.modules["src.runtime.jira_workflow_contract"] = contract_mod
    contract_spec.loader.exec_module(contract_mod)

    events_mod = types.ModuleType("src.runtime.events")
    events_mod.build_runtime_event = lambda **kwargs: dict(kwargs)

    modules = {
        "src": src_pkg,
        "src.runtime": runtime_pkg,
        "src.runtime.runtime_adapter_execution": runtime_adapter_mod,
        "src.runtime.jira_workflow_contract": contract_mod,
        "src.runtime.events": events_mod,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)

    src_pkg.runtime = runtime_pkg
    runtime_pkg.runtime_adapter_execution = runtime_adapter_mod
    runtime_pkg.jira_workflow_contract = contract_mod
    runtime_pkg.events = events_mod

    spec = importlib.util.spec_from_file_location("src.runtime.jira_workflow_review", Path("src/runtime/jira_workflow_review.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["src.runtime.jira_workflow_review"] = module
    spec.loader.exec_module(module)
    runtime_pkg.jira_workflow_review = module

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        sys.modules.pop("src.runtime.jira_workflow_review", None)

    return module, _cleanup
