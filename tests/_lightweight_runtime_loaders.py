from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def load_github_url_utils_lightweight():
    spec = importlib.util.spec_from_file_location("src.github.url_utils", Path("src/github/url_utils.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_jira_workflow_review_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []
    agents_pkg = types.ModuleType("src.agents")
    agents_pkg.__path__ = []
    runtime_pkg = types.ModuleType("src.runtime")
    runtime_pkg.__path__ = []

    executor_mod = types.ModuleType("src.agents.executor")

    class SkillResult:
        def __init__(self, success, output="", data=None, error=None):
            self.success = success
            self.output = output
            self.data = data or {}
            self.error = error

    async def execute_skill(*args, **kwargs):
        return SkillResult(success=True, output="ok", data={"approved": True})

    executor_mod.SkillResult = SkillResult
    executor_mod.execute_skill = execute_skill
    executor_mod.run_skill_execution = lambda *args, **kwargs: None

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
        "src.agents": agents_pkg,
        "src.runtime": runtime_pkg,
        "src.agents.executor": executor_mod,
        "src.runtime.runtime_adapter_execution": runtime_adapter_mod,
        "src.runtime.jira_workflow_contract": contract_mod,
        "src.runtime.events": events_mod,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)

    src_pkg.agents = agents_pkg
    src_pkg.runtime = runtime_pkg
    agents_pkg.executor = executor_mod
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


def load_confluence_init_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []
    confluence_pkg = types.ModuleType("src.confluence")
    confluence_pkg.__path__ = []

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []
    attachment_mod = types.ModuleType("src.utils.attachment")
    attachment_mod.download_and_process_attachment = None

    api_mod = types.ModuleType("src.confluence.api")

    class _Channel:
        base_url = "https://c"
        _auth_header = {}
        def is_configured(self):
            return True
        async def get_attachments(self, page_id):
            return []

    api_mod.ConfluenceChannel = object
    api_mod.confluence_channel = _Channel()

    adapter_mod = types.ModuleType("src.confluence.adapter")
    adapter_mod.ConfluenceFormatAdapter = lambda ch: types.SimpleNamespace()
    adapter_mod._extract_page_id_from_url = lambda url: "1"

    source_context_mod = types.ModuleType("src.source_context")
    source_context_mod.persist_confluence_source_bundle_and_digest = lambda **kwargs: {"context_ref": "c", "digest_ref": "d"}

    blob_mod = types.ModuleType("src.context_blob_store")
    blob_mod.put_text = lambda **kwargs: "ctx://context/s/k/sha"

    source_service_mod = types.ModuleType("src.confluence.source_service")
    source_service_mod.format_confluence_source_manifest = lambda source: "manifest"
    source_service_mod.prepare_confluence_page_source = None

    modules = {
        "src": src_pkg,
        "src.confluence": confluence_pkg,
        "src.utils": utils_pkg,
        "src.utils.attachment": attachment_mod,
        "src.confluence.api": api_mod,
        "src.confluence.adapter": adapter_mod,
        "src.source_context": source_context_mod,
        "src.context_blob_store": blob_mod,
        "src.confluence.source_service": source_service_mod,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)

    src_pkg.confluence = confluence_pkg
    src_pkg.utils = utils_pkg
    confluence_pkg.api = api_mod
    confluence_pkg.adapter = adapter_mod

    spec = importlib.util.spec_from_file_location("src.confluence", Path("src/confluence/__init__.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["src.confluence"] = module
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old

    return module, _cleanup
