from pathlib import Path


def test_p3_contract_docs_exist_and_are_indexed():
    assert Path("docs/runtime_contract.md").exists()
    assert Path("docs/runtime-design.md").exists()
    assert Path("docs/runtime-tool-surface.md").exists()
    assert Path("docs/observability_contract.md").exists()
    docs_index = Path("docs/README.md").read_text(encoding="utf-8")
    assert "runtime_contract.md" in docs_index
    assert "runtime-design.md" in docs_index
    assert "runtime-tool-surface.md" in docs_index
    assert "observability_contract.md" in docs_index
    root_readme = Path("README.md").read_text(encoding="utf-8")
    assert "docs/runtime_contract.md" in root_readme
    assert "docs/runtime-design.md" in root_readme
    assert "docs/runtime-tool-surface.md" in root_readme
    assert "docs/observability_contract.md" in root_readme


def test_runtime_contract_doc_mentions_current_native_contract_surfaces():
    text = Path("docs/runtime_contract.md").read_text(encoding="utf-8")
    for needle in [
        "/api/chat",
        "/api/chat/stream",
        "/api/events",
        "/api/capabilities",
        "/api/skills",
        "/api/tasks/execute",
        "/api/tasks/{task_id}",
        "/api/internal/runtime-profile/apply",
        "EFP_SKILLS_DIR",
        "/app/skills",
        "EFP runtime native mode supports GitHub Copilot only",
        "EFP-owned runtime built-in registry",
        "prebuilt `engineering-flow-platform-tools` CLI binaries",
        "`jira`, `confluence`, and `browser`",
        "<tool> commands --json",
        "Runtime profile application still projects Jira, Confluence, GitHub, and Git configuration",
        "Legacy Python tool packages",
        "Legacy `EFP_TOOLS_DIR` / `EFP_EXTERNAL_TOOLS_*` Python external tool loaders",
        "MCP servers",
        "tests/fixtures/runtime_contract",
    ]:
        assert needle in text


def test_runtime_design_doc_matches_removed_tool_and_portal_boundaries():
    text = Path("docs/runtime-design.md").read_text(encoding="utf-8")

    for needle in [
        "direct runtime",
        "Portal is the UI and control plane",
        "Workspace-local Python tool loaders are not available",
        "prebuilt `engineering-flow-platform-tools`",
        "cmd/<tool>",
        "`EFP_TOOLS_DIR` and",
        "MCP and external protocol tool servers are intentionally excluded",
        "RuntimeSessionManager",
        "EFP_RUNTIME_SESSION_ROOT",
    ]:
        assert needle in text

    for stale in [
        "efp_runtime.tools.external remains",
        "efp_runtime.tools.local` remains",
        "include_legacy_tool_aliases",
        "enable_local_python_tools",
        "toolSurface",
    ]:
        assert stale not in text


def test_runtime_tool_surface_doc_records_current_gaps():
    text = Path("docs/runtime-tool-surface.md").read_text(encoding="utf-8")

    for needle in [
        "Core loop/history/provider request",
        "Tools: bash/read/write/edit/apply_patch/grep/glob/webfetch/todowrite",
        "Skills discovery/activation/commands",
        "Session list/delete/fork/revert/summary/query/todos",
        "Context and automatic compaction",
        "Permissions and workspace-full-access defaults",
        "GitHub Copilot provider/model path",
        "MCP",
        "Intentional Remaining Gaps",
    ]:
        assert needle in text


def test_observability_contract_doc_mentions_all_log_context_fields():
    text = Path("docs/observability_contract.md").read_text(encoding="utf-8")
    for field in [
        "trace_id",
        "span_id",
        "parent_span_id",
        "request_id",
        "session_id",
        "task_id",
        "portal_task_id",
        "portal_dispatch_id",
        "agent_id",
        "runtime_type",
        "execution_type",
        "source_type",
        "tool_name",
        "tool_source",
        "skill_name",
        "profile_version",
        "path",
    ]:
        assert field in text
    assert "RedactingFilter" in text
    assert "third-party" in text or "第三方" in text
