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
    from tests._lightweight_source_service_loaders import load_confluence_source_service_lightweight

    source_service_module, source_cleanup = load_confluence_source_service_lightweight()

    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []
    attachment_mod = types.ModuleType("src.utils.attachment")
    attachment_mod.download_and_process_attachment = getattr(
        source_service_module,
        "_default_download_and_process_attachment",
        None,
    )

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

    modules = {
        "src": src_pkg,
        "src.utils": utils_pkg,
        "src.utils.attachment": attachment_mod,
        "src.confluence.api": api_mod,
        "src.confluence.adapter": adapter_mod,
        "src.source_context": source_context_mod,
        "src.context_blob_store": blob_mod,
        "src.confluence.source_service": source_service_module,
    }

    src_pkg.utils = utils_pkg
    src_pkg.utils = utils_pkg
    module, cleanup = _load_module_with_stubs("src.confluence", Path("src/confluence/__init__.py"), modules)
    src_pkg.confluence = module

    def _cleanup():
        cleanup()
        source_cleanup()

    return module, _cleanup


def load_jira_init_lightweight():
    from tests._lightweight_source_service_loaders import load_jira_source_service_lightweight

    source_service_module, source_cleanup = load_jira_source_service_lightweight()

    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []
    attachment_mod = types.ModuleType("src.utils.attachment")
    attachment_mod.download_and_process_attachment = getattr(
        source_service_module,
        "_default_download_and_process_attachment",
        None,
    )

    source_context_mod = types.ModuleType("src.source_context")
    source_context_mod.persist_jira_source_bundle_and_digest = lambda **kwargs: {
        "context_ref": "ctx://jira",
        "digest_ref": "ctx://jira/d",
        "source_digest_chunk_count": 0,
    }

    blob_mod = types.ModuleType("src.context_blob_store")
    blob_mod.put_text = lambda **kwargs: "ctx://context/s/k/sha"
    blob_mod.read_ref = lambda ref, **kwargs: '{"raw": true}'

    api_mod = types.ModuleType("src.jira.api")

    class _Channel:
        api_version = "3"
        _auth_header = {}

        def is_configured(self):
            return True

        def get_instance_client(self, **kwargs):
            return self

    async def _ok(*args, **kwargs):
        return "ok"

    api_mod.JiraChannel = object
    api_mod.jira_channel = _Channel()
    api_mod.jira_search = _ok
    api_mod.jira_add_attachment = _ok
    api_mod.jira_transition = _ok
    api_mod.jira_get_transitions = _ok
    api_mod.jira_assign_issue = _ok
    api_mod.jira_get_projects = _ok
    api_mod.jira_get_components = _ok
    api_mod.jira_get_versions = _ok
    api_mod.jira_get_worklog = _ok
    api_mod.jira_add_worklog = _ok
    api_mod.jira_get_comments = _ok
    api_mod.get_tools_schemas = lambda: []

    adapter_mod = types.ModuleType("src.jira.adapter")
    adapter_mod.JiraFormatAdapter = source_service_module.JiraFormatAdapter

    exporter_mod = types.ModuleType("src.jira.exporter")
    exporter_mod.jira_export_issues_to_markdown = _ok

    preview_mod = types.ModuleType("src.jira.attachment_preview")
    preview_mod.render_issue_attachment_previews = _ok

    modules = {
        "src": src_pkg,
        "src.utils": utils_pkg,
        "src.utils.attachment": attachment_mod,
        "src.source_context": source_context_mod,
        "src.context_blob_store": blob_mod,
        "src.jira.source_service": source_service_module,
        "src.jira.api": api_mod,
        "src.jira.adapter": adapter_mod,
        "src.jira.exporter": exporter_mod,
        "src.jira.attachment_preview": preview_mod,
    }
    module, cleanup = _load_module_with_stubs("src.jira", Path("src/jira/__init__.py"), modules)
    src_pkg.jira = module

    def _cleanup():
        cleanup()
        source_cleanup()

    return module, _cleanup


def load_root_execute_tool_lightweight():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    github_mod = types.ModuleType("src.github")
    github_mod.get_tools_schemas = lambda: []
    git_mod = types.ModuleType("src.git")
    git_mod.get_tools_schemas = lambda: []

    bash_mod = types.ModuleType("src.bash_tools")
    bash_mod.get_tools_schemas = lambda: []

    context_mod = types.ModuleType("src.context_tools")
    context_mod.get_tools_schemas = lambda: []
    context_mod.context_read_ref = lambda *args, **kwargs: ""

    async def _ok(*args, **kwargs):
        return "ok"

    jira_mod = types.ModuleType("src.jira")
    jira_mod.get_tools_schemas = lambda: []
    jira_mod.jira_get_issue_by_url = _ok
    jira_mod.jira_get_comments = _ok
    jira_mod.jira_export_issues_to_markdown = _ok

    confluence_mod = types.ModuleType("src.confluence")
    confluence_mod.get_tools_schemas = lambda: []
    confluence_mod.confluence_get_page_by_url = _ok
    confluence_mod.confluence_get_page_children = _ok
    confluence_mod.confluence_prepare_page_context = _ok
    confluence_mod.confluence_get_comments = _ok

    modules = {
        "src": src_pkg,
        "src.github": github_mod,
        "src.git": git_mod,
        "src.bash_tools": bash_mod,
        "src.context_tools": context_mod,
        "src.jira": jira_mod,
        "src.confluence": confluence_mod,
    }
    module, cleanup = _load_module_with_stubs("src", Path("src/__init__.py"), modules)
    return module, cleanup
